# 通信ルールと隔離の範囲

この環境は、作業用の`agent`、外部通信を検査する`proxy`、オフラインのDockerを提供する`dind`の3コンテナで構成します。agentから外部へ直接接続する経路はなく、通信はproxyを通ります。proxyを停止すると通信も失敗し、直通には切り替わりません。

## 許可される通信

| 接続先 | 許可する用途 |
|---|---|
| `api.github.com` | リポジトリ向けREST API、GraphQL、`GET /user` |
| `github.com` | Git smart HTTPによるclone、fetch、push |
| `api.anthropic.com` | Messages API、token count、Claude Codeの起動時の接続確認（`GET /api/hello`）、remote managed settingsの取得、Artifacts（公開・読取り・一覧などの`/api/frame/`以下。ただし共有範囲を変える`/api/frame/perm/`は除く。MCPサーバー`/api/artifacts/mcp`、その利用に必要なclaude.ai MCPサーバー一覧、組織のポリシー、アカウント情報の取得） |

GitHubの認証は、proxyに渡したfine-grained PATで上書きします。GraphQLの本文は解析せず、リポジトリへの書込みが許されるかはGitHubの認可に任せます。このため、PATは共有を許可した非公開リポジトリだけを対象にする必要があります。公開リポジトリの読取りは残ります。

GitHubのarchive、LFS、release assetや、package registryには対応していません。WebSocket、SOCKS、raw TCP、平文HTTPによる外部接続も拒否します。

Anthropicでは、Anthropicのサーバーから別の接続先へ通信させる指定だけを拒否します。対象はMCP connector、web search／fetchなどサーバー側で実行するtool、code executionのcontainer、URLやファイルIDで指定するcontentです。それ以外の項目やbeta headerは通すため、Claude Codeの新しい機能でproxyの更新は要りません。この4種類以外の形でサーバーから外部へ接続する機能が今後追加された場合は、それも通ります。

拒否した要求には通常HTTP 403を返します。本文付きの要求をheaderの段階で拒否した場合は、本文を受信し続けないよう接続を切断します。

## 認証とCA

本物のPATとAnthropic認証値、HTTPSの検査に使うCA秘密鍵はproxyだけが持ちます。認証値は起動後にホストの環境変数から注入コマンドの標準入力を通じて渡し、proxy専用tmpfsの0400ファイルに保存します。Dockerの作成設定やimageには含めません。認証値がない場合やファイルが不正な場合は要求を拒否し、以前の値には戻りません。agentにはダミーのOAuthトークンを環境変数で設定しています。proxyが外部へ接続する際に、本物の値へ置き換えます。API keyではなくOAuthトークンにしているのは、Claude CodeがAPI keyで動いているとclaude.aiにログインしていないと判断し、Artifactsを使わせないためです。proxyにAPI keyを注入した場合も推論は使えますが、Artifactsは使えません。

CA秘密鍵は専用volumeに保存し、agentには公開CAだけを渡します。公開CAはsystem trustに追加済みで、Node.js、Git、curl用の環境変数も設定しています。独自のtruststoreを使うツールでは、公開CAを別途追加してください。TLS検証を無効化しないでください。

## コンテナ内の実行権限

agentの起動処理はfirewallとCAを設定してから一般ユーザー（既定はUID 1000）へ切り替えます。通常のプロセスにはcapabilityを与えず、`no-new-privileges`を設定します。seccompとAppArmorはDockerの既定のままです。

ClaudeのBash sandboxは使いません。sandboxはBashの呼び出しごとに作り直され、終わるときに中で起動したプロセスも消えるので、`playwright-cli`のように呼び出しをまたいでデーモンを使うツールが動かないからです。

このため、Bashで実行したコマンドもClaude本体と同じくproxyを通り、`api.anthropic.com`にも届きます。proxyはその要求にも本物の認証値を付けます。proxyが拒否する上記の4種類以外の形で、Anthropicのサーバーから外部へ接続する機能が今後追加された場合、Bashのコマンドからそれを使って第三者へ送信できます。この経路は、VS Codeで人間が起動したビルドなど、agentで動くすべてのプロセスに元からあります。境界はコンテナとproxyで、Claude Codeの設定には頼りません。

managed settingsではWebSearchも禁止しています。検索結果は`api.anthropic.com`の応答としてモデルに届くので、取得元を通信の検査では判定できず、未信頼のWebページの内容が入り込むためです。サーバー側のtoolはproxyでも拒否しているので、この禁止は重ねがけです。

rootless DinDはprivilegedコンテナで動かします。同梱の[`dind-bridge`](../.devcontainer/claude/dind-bridge/README.md)がDocker APIと公開TCPポートをagentのloopbackへ中継します。

agentへmountするDocker APIのUnix socketはread-onlyですが、Docker APIの操作権限を読取り専用にはしません。ホストのDocker socketは共有しません。

## ホストが実行する設定の保護

agentが書き換えたものをホストが実行すると、通信の制限を外されたり、ホストで認証情報を盗まれたりします。そこで、ホストが実行・参照するものはagentへread-onlyでmountします。

| パス | ホストでの使われ方 |
|---|---|
| `.devcontainer/` | compose設定、proxyの通信ルール、`initializeCommand`、認証情報の注入、`agent-setup.sh`。次の起動時にホストがビルド・実行する |
| `.git/config` | `core.fsmonitor`、`core.hooksPath`、`filter.*`、`include.path`など、ホストのGitにコマンドを実行させる設定を書ける |
| `.git/hooks/` | ホストのGitが実行する |
| `.git/modules/` | サブモジュールのconfigとhooks |

`.git`自体も別にmountしているので、agentは`.git`を置き換えられません。`objects`や`refs`などは書き込めるので、agentはコミット、ブランチの作成、pushができます。`.git/config`へは書き込めないので、`git push -u`や`git remote add`は失敗します。push先は`git push origin HEAD`のように明示してください。サブモジュールの追加・更新もできません。

このため、作業ディレクトリはGitリポジトリのルートである必要があります。`git worktree`で作った作業ツリーには対応していません。

ホストの設定（`~/.claude`の一部と`~/.config/claude-devcontainer/extra/`）は、ホストからagentへコピーするだけで、mountもしませんし、逆向きにも戻しません。`~/.claude`をmountすると、agentがhookを書き込んで、ホストのClaude Codeに実行させられるからです。コピーの対象は名前を決めたものだけです。`.credentials.json`やセッション履歴はコピーしません。

### 人間が気付く必要があるもの

agentは、作業ツリーを書き換えずに、Gitの操作で`.devcontainer/`を変更したコミットを作ったり、indexに載せたりできます。それをホストで`git restore`、`checkout`、`reset --hard`、`pull`などで反映すると、次の`devcontainer up`で実行されます。同じように、agentが途中で止めたrebaseのtodoに`exec`行があると、ホストで`git rebase --continue`したときに実行されます。

どちらも仕組みでは止められないので、反映する前に気付く必要があります。`.devcontainer/`がHEADやindexと異なる状態は`git status`に出ます。シェルのプロンプトに警告を出しておくと見落としにくくなります。starshipを使う場合の設定例です。

```toml
# ~/.config/starship.toml
[custom.devcontainer_drift]
when = """
top=$(git rev-parse --show-toplevel 2>/dev/null) && [ -d "$top/.devcontainer" ] &&
! { git -C "$top" diff --quiet HEAD -- .devcontainer && git -C "$top" diff --quiet -- .devcontainer; }
"""
shell = ["sh"]
format = "[⚠ .devcontainer differs from HEAD/index ]($style)"
style = "bold red"
```

Powerlevel10kを使う場合は、`~/.p10k.zsh`に次の関数を足し、`POWERLEVEL9K_LEFT_PROMPT_ELEMENTS`に`devcontainer_drift`を加えます。

```zsh
# ~/.p10k.zsh
function prompt_devcontainer_drift() {
  local top
  top=$(git rev-parse --show-toplevel 2>/dev/null) && [[ -d $top/.devcontainer ]] || return
  git -C $top diff --quiet HEAD -- .devcontainer && git -C $top diff --quiet -- .devcontainer && return
  p10k segment -f red -t '⚠ .devcontainer differs from HEAD/index'
}
```

rebaseの途中であることは、starshipでは標準の`git_state`モジュールが、Powerlevel10kでは標準の`vcs`セグメントが表示します。

## 保護の対象外

通信制限が適用されるのはコンテナ内で実行するプログラムです。共有したソースコードをホスト上で実行しても、この制限では保護できません。ホストのDocker操作権限は強い権限を持つため、ホストで使うスクリプトや起動設定はレビューしてから実行してください。

作業ツリー全体がagentへ共有されます。この環境は、共有ファイルに含まれる秘密の読取りを防ぐものではありません。ホストのホーム、Docker socket、秘密のディレクトリを追加mountしないでください。Claudeが受け取る入力元の制限も対象外です。

VS Codeから使う場合には、エディタを通じたホスト認証の転送にも注意が必要です。[VS Codeの利用条件](usage.md#vs-codeで開く)を参照してください。

## 通信ルールを変更する

ルールは[`proxy/addon.py`](../.devcontainer/claude/proxy/addon.py)に固定しています。proxyのimageをビルドするときに組み込むので、ファイルを編集するだけでは反映されません。agentからは編集できません。

追加先の認証方法と送信先の制限をレビューしてから、READMEの停止手順で環境を停止し、ホストで取り込んだリポジトリのルートで次を実行します。

```sh
devcontainer build
devcontainer up
python3 .devcontainer/claude/scripts/inject-credentials.py
```

Anthropicの要求本文は全体を受信してから検査・転送します。AnthropicとGitの要求本文には上限を設けていないため、大きな要求や同時接続でメモリを使い切る可能性が残ります。proxyのメモリ上限は512 MiBで、停止した場合は通信も停止します。

[READMEに戻る](../README.md)
