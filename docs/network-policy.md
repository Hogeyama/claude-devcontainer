# 通信ルールと隔離の範囲

この環境は、作業用の`agent`、外部通信を検査する`proxy`、オフラインのDockerを提供する`dind`の3コンテナから成ります。agentから外部へ直接接続する経路はなく、通信はproxyを通ります。proxyを停止すると通信も失敗し、直通には切り替わりません。

## 許可される通信

Anthropicへの通信は[`proxy/addon.py`](../.devcontainer/claude/proxy/addon.py)に固定しています。それ以外は、リポジトリの`.devcontainer/claude.toml`の`proxy.hosts`に書いたものだけを通します（[接続先を追加する](#接続先を追加する)）。表のGitHubの行は、テンプレートの[`template/claude.toml`](../.devcontainer/claude/template/claude.toml)の内容です。

| 接続先 | 許可する用途 |
|---|---|
| `api.github.com` | テンプレートの`proxy.hosts`。リポジトリ向けREST API、GraphQL、`GET /user` |
| `github.com` | テンプレートの`proxy.hosts`。Git smart HTTPによるclone、fetch、push |
| `api.anthropic.com` | Messages API、token count、Claude Codeの起動時の接続確認（`GET /api/hello`）、remote managed settingsの取得、Artifacts（公開・読取り・一覧などの`/api/frame/`以下。ただし共有範囲を変える`/api/frame/perm/`は除く。MCPサーバー`/api/artifacts/mcp`、その利用に必要なclaude.ai MCPサーバー一覧、組織のポリシー、アカウント情報の取得） |
| `mcp-proxy.anthropic.com` | `[proxy]`の`connectors`で指定したclaude.aiのコネクタだけ。既定では許可しない。[claude.aiのコネクタを使う](#claudeaiのコネクタを使う)を参照 |

テンプレートの`proxy.hosts`では、proxyはGitHubへの要求の認証を、ホストの`GITHUB_TOKEN`（fine-grained PAT）によるBasic認証で上書きします。proxyはGraphQLの本文を解析せず、リポジトリへの書込みが許されるかはGitHubの認可に任せます。このため、PATは共有を許可した非公開リポジトリだけを対象にする必要があります。公開リポジトリの読取りは残ります。

GitHubのarchive、LFS、release assetや、package registryには対応していません。proxyはWebSocket、SOCKS、raw TCP、平文HTTPによる外部接続も拒否します。

proxyはAnthropic向けの要求では、Anthropicのサーバーから別の接続先へ通信させる指定だけを拒否します。対象はMCP connector、web search／fetchなどサーバー側で実行するtool、code executionのcontainer、URLやファイルIDで指定するcontentです。claude.aiのコネクタは、明示的に指定したものだけ例外として通します。サーバー側のtoolのうち、Anthropicのサーバー内でモデルを呼ぶだけのadvisor（`advisor_20260301`）は、他の接続先に接続しないので通します。新しい版のadvisorは、確認するまで拒否します。その間は、Claude Codeで`/advisor off`にすると推論を続けられます。それ以外の項目やbeta headerは通すため、Claude Codeの新しい機能でproxyの更新は要りません。この4種類以外の形でサーバーから外部へ接続する機能が今後追加された場合は、それも通ります。

proxyは拒否した要求に通常HTTP 403を返します。本文付きの要求をheaderの段階で拒否した場合は、本文を受信し続けないよう接続を切断します。

## 認証とCA

本物の秘密（`proxy.hosts`が参照するGitHubのPATなど）とAnthropic認証値、HTTPSの検査に使うCA秘密鍵はproxyだけが持ちます。ホストの注入スクリプトは、起動済みのproxy内の認証保存コマンドへ標準入力で認証値を渡します。proxy内の認証保存コマンドが、proxy専用tmpfsの0400ファイルに保存します。Dockerの作成設定やimageには含めません。proxyは認証値がない場合やファイルが不正な場合に要求を拒否し、以前の値には戻りません。Composeの設定では、agentの環境変数にダミーのOAuthトークンを指定しています。proxyが外部へ接続する際に、本物の値へ置き換えます。API keyではなくOAuthトークンにしているのは、Claude CodeがAPI keyで動いているとclaude.aiにログインしていないと判断し、Artifactsを使わせないためです。proxyにAPI keyを注入した場合も推論は使えますが、Artifactsは使えません。

proxyはCA秘密鍵を専用volumeに保存し、agentには公開CAだけを渡します。agentの起動処理が公開CAをsystem trustに追加します。agentのDockerfileには、Node.js、Git、curlがこのCAを使うための環境変数を設定しています。独自のtruststoreを使うツールでは、公開CAを別途追加してください。TLS検証を無効化しないでください。

## コンテナ内の実行権限

agentの起動処理はfirewallとCAを設定してから一般ユーザー（既定はUID 1000）へ切り替えます。この起動処理は通常のプロセスにcapabilityを与えず、`no-new-privileges`を設定します。seccompとAppArmorはDockerの既定のままです。

同梱の設定では、ClaudeのBash sandboxを有効にしていません。sandboxはBashの呼び出しごとに作り直され、終わるときに中で起動したプロセスも消えるので、`playwright-cli`のように呼び出しをまたいでデーモンを使うツールが動かないからです。

このため、Bashで実行したコマンドもClaude本体と同じくproxyを通り、`api.anthropic.com`にも届きます。proxyはその要求にも本物の認証値を付けます。proxyが拒否する上記の4種類以外の形で、Anthropicのサーバーから外部へ接続する機能が今後追加された場合、Bashのコマンドからそれを使って第三者へ送信できます。この経路は、VS Codeで人間が起動したビルドなど、agentで動くすべてのプロセスに元からあります。境界はコンテナとproxyで、Claude Codeの設定には頼りません。

managed settingsではWebSearchも禁止しています。検索結果は`api.anthropic.com`の応答としてモデルに届くので、取得元を通信の検査では判定できず、未信頼のWebページの内容が入り込むためです。サーバー側のtoolはproxyでも拒否しているので、この禁止は重ねがけです。

同梱のCompose設定は、rootless DinDをprivilegedコンテナで起動します。同梱の[`dind-bridge`](../.devcontainer/claude/dind-bridge/README.md)がDocker APIと公開TCPポートをagentのloopbackへ中継します。

agentへmountするDocker APIのUnix socketはread-onlyですが、Docker APIの操作権限を読取り専用にはしません。同梱のCompose設定は、ホストのDocker socketを共有しません。

## ホストが実行する設定の保護

agentは専用のDocker volumeにある独立cloneで作業します。ホストの作業ツリー、index、refs、Git設定、hooksをmountしません。agentが自分のindexに`assume-unchanged`を付けても、ホストのレビューには影響しません。

ホストの生成物は`/run/host/generated`、Nixの環境は`/run/host/nix-env`にread-onlyで渡します。コンテナの管理スクリプトはこれらを読み、clone内の`.devcontainer/`を管理用入力として使いません。Gitの初期化・取得は権限を落としたnodeで行います。ホストからGitを実行する場合も`docker exec --user node`を使ってください。

Nodeの依存はCoWを使います。ホストのlowerへの書き戻しはなく、agentの変更はupperへ残ります。Gradleの既存CoWも維持します。利用中にホストのlowerを変更しないでください。[依存の共有と更新](usage.md#依存の共有と更新)を参照してください。

ホストの設定（`~/.claude`の一部と`~/.config/claude-devcontainer/extra/`）は、従来どおりホストからagentへ一方向にコピーします。ホームや認証情報を丸ごとmountしません。

### コミットをレビューして取り込む

共通リモートのブランチはagentも更新できます。pushされたことを承認済みとは扱いません。ホストでfetch後、信頼するベースと候補のコミットIDを固定して比較し、レビューしたIDだけを`git merge --ff-only`で取り込みます。[具体的な手順](usage.md#ホストでレビューして取り込む)を参照してください。

`.devcontainer/`、`package.json`、`.envrc`、ビルドスクリプトなど、ホストでコードが実行される変更を確認します。IDEで開く、別worktreeへcheckoutする、ホストでテストする場合も先にレビューします。リモートのrefが後から変わっても、レビュー対象のコミットIDは変えません。

## 保護の対象外

通信制限が適用されるのはコンテナ内で実行するプログラムです。取り込んだソースコードをホスト上で実行しても、この制限では保護できません。ホストのDocker操作権限は強い権限を持つため、ホストで使うスクリプトや起動設定はレビューしてから実行してください。

コミットに含まれるファイルはagentへ渡ります。未コミット・未追跡ファイルは転送しませんが、Gitに入れた秘密の読取りは防げません。ホストのホーム、Docker socket、秘密のディレクトリを追加mountしないでください。Claudeが受け取る入力元の制限も対象外です。

VS Codeから使う場合には、エディタを通じたホスト認証の転送にも注意が必要です。[VS Codeの利用条件](usage.md#vs-codeで開く)を参照してください。

## 通信ルールを変更する

Anthropicへのルールは[`proxy/addon.py`](../.devcontainer/claude/proxy/addon.py)に固定しています。それ以外の接続先は、リポジトリの`.devcontainer/claude.toml`の`proxy.hosts`に書きます。どちらもproxyのimageをビルドするときに組み込むので、ファイルを編集するだけでは反映されません。clone内の設定はagentが編集できますが、ホストへレビューして取り込むまで反映しません。

追加先の認証方法と送信先の制限をレビューしてから、READMEの停止手順で環境を停止し、`devcontainer up`で起動し直してください。proxyのimageは起動のたびにビルドし直されます。変更がなければキャッシュが使われます。`devcontainer build`がビルドするのはagentのimageだけなので、proxyの変更には使えません。

### 接続先を追加する

`.devcontainer/claude.toml`に、接続先のホストごとに`[proxy.hosts."ホスト名"]`のテーブルを作り、通す要求と、proxyが付ける認証を書きます。ホスト名には`.`が含まれるので、引用符で囲んでください。テンプレートは[`template/claude.toml`](../.devcontainer/claude/template/claude.toml)にあり、GitHubの設定が入っています。

```toml
[proxy.hosts."fonts.googleapis.com"]           # 認証の要らない公開ホスト
routes = ["GET /css2?"]

[proxy.hosts."api.notion.com"]                 # トークンの要るAPI
routes = ["/v1/.+"]                            # methodを省くとすべてのmethod
headers = ["Notion-Version"]
inject = { Authorization = "Bearer ${NOTION_API_KEY}" }
env = ["NOTION_API_KEY"]

[proxy.hosts."redmine.my-company.com"]
routes = ['GET /issues\.json']                 # 単一引用符の文字列は\をそのまま書ける
inject = { X-Redmine-API-Key = "${REDMINE_API_KEY}" }
```

- `routes`には、通す要求を`GET /path`か、pathだけ（すべてのmethod）で書きます。pathはqueryを除いたURLのpath全体に一致させる正規表現です。`/.*`のように広く書くと、そのホストのどのAPIにも届きます。
- ホスト名は小文字のDNS名だけです。ワイルドカード、IPアドレス、port番号は書けません。portは443、HTTPSに限ります。
- `headers`には、agentが送ってよいheaderを書きます。`Accept`や`Content-Type`などの一般的なものは書かなくても通ります。`Authorization`や`Cookie`などの認証は書けません。agentが付けた認証は常に除きます。
- `inject`には、proxyが付けるheaderを書きます。agentが付けた同名のheaderは置き換えます。`${NAME}`は秘密の参照です。値を`{ basic = "user:password" }`にするとBasic認証になります（テンプレートのGitHubの設定を参照）。`{ … }`のインラインテーブルは1行に書く必要があります。headerが多い場合は、`[proxy.hosts."api.notion.com".inject]`のテーブルに分けて書けます。
- `env`には、agentにダミー値`managed-by-proxy`で渡す環境変数の名前を書きます（下記）。
- `api.anthropic.com`と`mcp-proxy.anthropic.com`は書けません。proxyがAnthropicの認証値を付ける接続先で、ルールは`addon.py`に固定しています。

秘密の値は、ホストの`~/.config/claude-devcontainer/credentials`に、他の認証情報と同じくシェルの代入で書きます。

```sh
NOTION_API_KEY=$(secret-tool lookup service notion)
REDMINE_API_KEY=$(pass work/redmine)
```

注入スクリプトは、起動したproxyに`proxy.hosts`が参照する秘密の名前を問い合わせ、このファイルで代入された変数のうち、その名前のものだけを渡します。ホストの環境にもともとある変数は渡しません。`proxy.hosts`が参照する秘密がこのファイルにない場合、proxyはその接続先への要求を、足りない秘密の名前を示して拒否します。

アプリケーションは、トークンが環境変数にないと要求を送らないことがあります。その場合は、ホストの`env`にアプリケーションが読む環境変数の名前を書いてください。agentにはダミー値`managed-by-proxy`が渡り、proxyが本物に置き換えます。テンプレートのGitHubの設定では、`gh`が読む`GH_TOKEN`を渡しています。秘密の名前（`GITHUB_TOKEN`）とアプリケーションが読む名前は違ってもかまいません。`HTTP_PROXY`や`DOCKER_HOST`など、Composeがagentに設定する名前は書けません。

agentのNode.jsの`fetch`も、`NODE_USE_ENV_PROXY=1`によりproxyを通ります。Node.js 22では実験的機能として警告が1行出ます。

#### 送信先を決めるのはリポジトリ

どの秘密をどの接続先へ付けるかは、リポジトリの`claude.toml`が決めます。`.devcontainer/`を変えるコミットを取り込むと、ホストの秘密が別の接続先へ送られえます。`agent-setup.sh`がホストでrootで実行されるのと同じく、`.devcontainer/`の変更はレビューしてから取り込んでください（[コミットをレビューして取り込む](#コミットをレビューして取り込む)）。認証情報のファイルには、agentに使わせてよい秘密だけを置いてください。

要求のpathとqueryには、agentが任意のデータを載せられます。追加した接続先は、そのまま外部への送信経路になります。認証を付けた接続先では、agentはそのトークンの権限でできることをすべてできます。トークンの権限は必要な範囲に絞ってください。

`proxy.hosts`が不正な場合は、`devcontainer up`の起動処理が、ファイルと項目を示して失敗します。

### ホストだけの接続先を追加する

Redmineなど、リポジトリを使う人のうち一部だけが使う接続先は、`.devcontainer/claude.local.toml`に書けます。書き方は`claude.toml`と同じです。テンプレートの`.gitignore`で除外しているので、コミットされません。

```toml
[proxy.hosts."redmine.my-company.com"]
routes = ['GET /issues\.json']
inject = { X-Redmine-API-Key = "${REDMINE_API_KEY}" }
```

`claude.local.toml`のホストは`claude.toml`のホストに追加されます。同じホストが両方にある場合は、`claude.local.toml`の設定でホストごと置き換えます。`routes`、`headers`、`inject`、`env`は混ぜません。秘密は`claude.toml`と同じく、ホストの`~/.config/claude-devcontainer/credentials`に書きます。

`claude.toml`と同じくimageのビルド時に組み込みます。編集したら、環境を停止して`devcontainer up`で起動し直してください。不正な場合は起動処理が失敗し、`claude.local.toml:`で始まる理由を表示します。ホストの未追跡ファイルなのでcloneには入りません。秘密の値はこのファイルに書かず、`${NAME}`で参照してください。

### 社内CAを信頼させる

社内CAの証明書を使うホストへは、proxyが接続先の証明書を検証できず、502を返します。ホストにあるCA証明書（PEM、複数の証明書を連結したものも可）のパスを、`claude.toml`か`claude.local.toml`の`[proxy]`の`ca`に書いてください。相対パスは`.devcontainer/`が基準です。

```toml
[proxy]
ca = "/etc/ssl/certs/company-root.pem"
```

proxyは起動時にこのファイルを読み取り専用でmountし、公開のルート証明書に加えて信頼します。CA証明書でないものが含まれていると、proxyは理由を表示して起動しません。imageには組み込まないので、再ビルドは要りません。ファイルを変えたら、環境を停止して`devcontainer up`で起動し直してください。

信頼はホストごとに分けられず、すべての接続先に及びます。このCAの秘密鍵を持つ者は`api.github.com`などの証明書も偽造でき、proxyとの間で認証値を盗み見られます。信頼できる組織のCAだけを指定してください。

### claude.aiのコネクタを使う

Claude Codeは、claude.aiで接続したコネクタ（Google Driveなど）を`mcp-proxy.anthropic.com`経由で使います。Anthropicのサーバーが、接続済みのアカウントの権限でDriveなどを操作します。proxyは既定でこの接続を拒否し、`[proxy]`の`connectors`に書いたコネクタだけを、claude.aiの認証値を付けて通します。

コネクタは、ホストのClaude Codeのログイン（[ホストのClaude Codeのログイン](usage.md#ホストのclaude-codeのログイン)）で使えます。API keyでは使えません。

コネクタのIDはアカウントごとに異なります。agentのシェルで一覧を表示してください。

```sh
curl -s 'https://api.anthropic.com/v1/mcp_servers?limit=1000' -H 'anthropic-beta: mcp-servers-2025-12-04' \
  | jq -r '.data[] | "\(.id)\t\(.display_name)"'
```

使うIDを`.devcontainer/claude.local.toml`の`[proxy]`の`connectors`に書き、READMEの停止手順で環境を停止してから`devcontainer up`で起動し直してください。imageの再ビルドは要りません。起動時に認証値も注入し直されます。IDの形式が不正な場合は、起動処理が理由を表示して失敗します。

```toml
[proxy]
connectors = ["mcpsrv_01AbCdEf"]
```

指定していないコネクタはproxyが拒否するので、Claude Codeでは接続失敗と表示されます。

コネクタにできる操作はproxyでは検査しません。agentは、指定したコネクタの機能をアカウントの権限の範囲ですべて使えます。書込みや共有ができるコネクタは、ファイルの作成や公開を通じた外部への送信経路になります。また、コネクタが返す内容は未信頼の入力としてモデルに届きます。読取りだけで足りる用途のコネクタを選んでください。

proxyはAnthropicの要求本文全体を受信してから検査・転送します。AnthropicとGitの要求本文には上限を設けていないため、大きな要求や同時接続でメモリを使い切る可能性が残ります。proxyのメモリ上限は512 MiBで、停止した場合は通信も停止します。

[READMEに戻る](../README.md)
