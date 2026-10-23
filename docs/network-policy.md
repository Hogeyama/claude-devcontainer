# 通信ルールと隔離の範囲

この環境は、作業用の`agent`、外部通信を検査する`proxy`、オフラインのDockerを提供する`dind`の3コンテナから成ります。agentから外部へ直接接続する経路はなく、通信はproxyを通ります。proxyを停止すると通信も失敗し、直通には切り替わりません。

## 許可される通信

Anthropicへの通信は[`proxy/addon.py`](../.devcontainer/claude/proxy/addon.py)に固定しています。それ以外は、リポジトリの`.devcontainer/claude.toml`の`proxy.hosts`に書いたものだけを通します（[接続先を追加する](#接続先を追加する)）。表のGitHubの行は、テンプレートの[`template/claude.toml`](../.devcontainer/claude/template/claude.toml)の内容です。

| 接続先 | 許可する用途 |
|---|---|
| `api.github.com` | テンプレートの`proxy.hosts`。リポジトリ向けREST API、GraphQL、`GET /user` |
| `github.com` | テンプレートの`proxy.hosts`。Git smart HTTPによるclone、fetch、push |
| `api.anthropic.com` | Messages API、token count、Claude Codeの起動時の接続確認（`GET /api/hello`）、remote managed settingsの取得、Artifacts（公開・読取り・一覧などの`/api/frame/`以下。ただし共有範囲を変える`/api/frame/perm/`は除く。MCPサーバー`/api/artifacts/mcp`、その利用に必要なclaude.ai MCPサーバー一覧、組織のポリシー、アカウント情報の取得） |
| `*.frame.claudeusercontent.com` | Artifactsの本文の読取り（`GET /_f/`以下）と、その前の接続確認（`GET /_warmup`）。サブドメインはArtifactごとに異なる。読取り用のトークンはURLのクエリにあるので、proxyは認証値を付けない。agentはサブドメイン、パス、クエリに任意のデータを入れられるが、受け取るのはAnthropicのサーバーだけ |
| `mcp-proxy.anthropic.com` | `[proxy]`の`connectors`で指定したclaude.aiのコネクタだけ。既定では許可しない。[claude.aiのコネクタを使う](#claudeaiのコネクタを使う)を参照 |

テンプレートの`proxy.hosts`では、proxyはGitHubへの要求の認証を、ホストの`GITHUB_TOKEN`（fine-grained PAT）によるBasic認証で上書きします。proxyはGraphQLの本文を解析せず、リポジトリへの書込みが許されるかはGitHubの認可に任せます。このため、PATは共有を許可した非公開リポジトリだけを対象にする必要があります。公開リポジトリの読取りは残ります。

GitHubのarchive、LFS、release assetや、package registryには対応していません。proxyはWebSocket、SOCKS、raw TCPによる外部接続も拒否します。平文HTTPは、`proxy.hosts`で`http = true`を書いた接続先にだけ通します（[接続先を追加する](#接続先を追加する)）。

proxyはAnthropic向けの要求では、Anthropicのサーバーから別の接続先へ通信させる指定を既定で拒否します。対象はMCP connector、web search／fetchなどサーバー側で実行するtool、code executionのcontainer、URLやファイルIDで指定するcontentです。WebSearchは[`proxy.web_search`](#websearchを使う)を有効にした場合、claude.aiのコネクタは明示的に指定したものだけ例外として通します。サーバー側のtoolのうち、Anthropicのサーバー内でモデルを呼ぶだけのadvisor（`advisor_20260301`）は、他の接続先に接続しないので通します。新しい版のadvisorは、確認するまで拒否します。その間は、Claude Codeで`/advisor off`にすると推論を続けられます。それ以外の項目やbeta headerは通すため、Claude Codeの新しい機能でproxyの更新は要りません。この4種類以外の形でサーバーから外部へ接続する機能が今後追加された場合は、それも通ります。

proxyは拒否した要求に通常HTTP 403を返します。本文付きの要求をheaderの段階で拒否した場合は、本文を受信し続けないよう接続を切断します。

## 認証とCA

本物の秘密（`proxy.hosts`が参照するGitHubのPATなど）とAnthropic認証値、HTTPSの検査に使うCA秘密鍵はproxyだけが持ちます。ホストの注入スクリプトは、起動済みのproxy内の認証保存コマンドへ標準入力で認証値を渡します。proxy内の認証保存コマンドが、proxy専用tmpfsの0400ファイルに保存します。Dockerの作成設定やimageには含めません。proxyは認証値がない場合やファイルが不正な場合に要求を拒否し、以前の値には戻りません。Composeの設定では、agentの環境変数にダミーのOAuthトークンを指定しています。proxyが外部へ接続する際に、本物の値へ置き換えます。API keyではなくOAuthトークンにしているのは、Claude CodeがAPI keyで動いているとclaude.aiにログインしていないと判断し、Artifactsを使わせないためです。proxyにAPI keyを注入した場合も推論は使えますが、Artifactsは使えません。

proxyはCA秘密鍵を専用volumeに保存し、agentには公開CAだけを渡します。agentの起動処理が公開CAをsystem trustに追加します。agentのDockerfileには、Node.js、Git、curlがこのCAを使うための環境変数を設定しています。Javaには、起動処理がsystem trustをJKSに変換した`/etc/ssl/java/cacerts`を作り、`JAVA_TOOL_OPTIONS`の`-Djavax.net.ssl.trustStore`で指定しています。Javaは`HTTPS_PROXY`も読まないため、同じ`JAVA_TOOL_OPTIONS`でproxy（`-Dhttps.proxyHost`など）と、proxyを通さない宛先（`-Dhttp.nonProxyHosts`、localhostとDinD）も指定しています。このためJavaは起動のたびに`Picked up JAVA_TOOL_OPTIONS`を標準エラーに出します。ほかに独自のtruststoreを使うツールでは、公開CAを別途追加してください。TLS検証を無効化しないでください。

## コンテナ内の実行権限

agentの起動処理はfirewallとCAを設定してから一般ユーザー（既定はUID 1000）へ切り替えます。この起動処理は通常のプロセスにcapabilityを与えず、`no-new-privileges`を設定します。seccompとAppArmorはDockerの既定のままです。

同梱の設定では、ClaudeのBash sandboxを有効にしていません。sandboxはBashの呼び出しごとに作り直され、終わるときに中で起動したプロセスも消えるので、`playwright-cli`のように呼び出しをまたいでデーモンを使うツールが動かないからです。

このため、Bashで実行したコマンドもClaude本体と同じくproxyを通り、`api.anthropic.com`にも届きます。proxyはその要求にも本物の認証値を付けます。proxyが拒否する上記の4種類以外の形で、Anthropicのサーバーから外部へ接続する機能が今後追加された場合、Bashのコマンドからそれを使って第三者へ送信できます。この経路は、VS Codeで人間が起動したビルドなど、agentで動くすべてのプロセスに元からあります。境界はコンテナとproxyで、Claude Codeの設定には頼りません。

WebSearchはproxyが既定で拒否します。[`proxy.web_search`](#websearchを使う)で許可できます。同梱のmanaged settingsによるWebSearchの禁止はありません。

WebFetchは禁止していません。WebFetchではagentのClaude Codeが自分でURLを取得するので、ほかの通信と同じくproxyの制限を受け、`proxy.hosts`に書いた接続先にしか届きません。なお、Claude CodeはURLを取得する前に、接続先のホスト名を`api.anthropic.com`の`/api/web/domain_info`へ問い合わせます。proxyはこの要求を通さないので、settingsで`skipWebFetchPreflight`を`true`にしていなければ、WebFetchは失敗します。

同梱のCompose設定は、rootless DinDをprivilegedコンテナで起動します。dockerdはagentのUIDで動き、DinDの中のコンテナはuser namespaceの中で動きます。それでもagentは、Docker APIで`--privileged`や`--security-opt seccomp=unconfined`を付けたコンテナを起動でき、その中ではagent自身より多くのカーネルの機能を呼べます。DinDのコンテナはprivilegedなので、seccomp、AppArmor、デバイスの制限がありません。そのため、カーネルに脆弱性があれば、agentはDinDを経てホストのrootを取れます。Dockerを使わないリポジトリでは、`docker.enabled`を有効にしないでください。

同梱の[`dind-bridge`](../.devcontainer/claude/dind-bridge/README.md)がDocker APIと公開TCPポートをagentのloopbackへ中継します。

agentへmountするDocker APIのUnix socketはread-onlyですが、Docker APIの操作権限を読取り専用にはしません。同梱のCompose設定は、ホストのDocker socketを共有しません。

## ホストが実行する設定の保護

この節は既定の`git.isolation = "clone"`に適用されます。agentは専用のDocker volumeにある独立cloneで作業します。ホストの作業ツリー、index、refs、Git設定、hooksをmountしません。agentが自分のindexに`assume-unchanged`を付けても、ホストのレビューには影響しません。

初回起動用のGitコピーは、ホストのローカルHEADと到達可能な履歴だけを独立したbare repositoryへ取り出します。ホストのGit設定、hooks、index、未コミット・未追跡ファイル、共有ハードリンクやalternatesは渡しません。初回取得にpushやネットワーク接続は不要です。

ホストの生成物は`/run/host/generated`、Nixの環境は`/run/host/nix-env`にread-onlyで渡します。コンテナの管理スクリプトはこれらを読み、clone内の`.devcontainer/`を管理用入力として使いません。Gitの初期化・取得は権限を落としたnodeで行います。ホストからGitを実行する場合も`docker exec --user node`を使ってください。

Nodeの依存はCoWを使います。ホストのlowerへの書き戻しはなく、agentの変更はupperへ残ります。Gradleの既存CoWも維持します。利用中にホストのlowerを変更しないでください。[依存の共有と更新](usage.md#依存の共有と更新)を参照してください。

ホストの設定（`~/.claude`の一部と`~/.config/claude-devcontainer/extra/`）は、従来どおりホストからagentへ一方向にコピーします。ホームや認証情報を丸ごとmountしません。

### コミットをレビューして取り込む

起動時に、ホストの`agent`リモートをコンテナ内リポジトリへ自動接続します。Gitの`ext`転送とDocker execを使い、Gitプロセスはコンテナ内のnodeで実行します。GitHub・proxy・SSHサーバーを経由しません。コンテナのブランチはagentが更新できますが、その内容を承認済みとは扱いません。ホストでfetch後、信頼するベースと候補のコミットIDを固定して比較し、レビューしたIDだけを`git merge --ff-only`で取り込みます。[具体的な手順](usage.md#ホストでレビューして取り込む)を参照してください。

取り込んだ後にホストで実行されるファイルの変更を確認します。対象のファイルは[ホストでレビューして取り込む](usage.md#ホストでレビューして取り込む)に挙げています。IDEで開く、別worktreeへcheckoutする、ホストでテストする場合も先にレビューします。リモートのrefが後から変わっても、レビュー対象のコミットIDは変えません。

### sharedで共有されるもの

`[git] isolation = "shared"`はホストの作業ツリーをread-writeで共有し、`/workspace/.git`にはagent専用のnamed volumeを重ねます。ホストの`.git`は`/run/host/git`にread-onlyで置きます。agentはadd・commit・switch・stashを自分の`.git`に対して行い、ホストのindex、refs、`refs/replace/`、`objects/info/alternates`、Git設定、hooksには書けません。agentが自分の`.git/config`にhooksや`core.fsmonitor`を書いても、それを実行するのはコンテナ内のnodeです。ホストは`git fetch agent`でagentのコミットを受け取り、レビューしたコミットIDを`git reset --mixed`でHEADとindexに反映します（[手順](usage.md#ホストでレビューして取り込む)）。objectsはcloneと同じローカルのコピーから初期化し、ホストのobjectsを参照しないので、ホストの`git gc`で壊れません。

sharedでもagentは作業ツリーのサブディレクトリに`.git`（ディレクトリまたは`gitdir:`ファイル）を作れます。ホストでそのディレクトリに入ってGitを実行すると、その`config`の`core.fsmonitor`やhooksが動きます。agentはいつでも新しく作れるので、ホストでサブディレクトリのGitを実行する直前に`find . -path ./.git -prune -o -name .git -print`で確認してください。agentの`git switch`や`stash`は共有作業ツリーのファイルを差し替えます。ホストの`.git`は壊れませんが、ホストで編集中のファイルは消えたり戻ったりします。

`.devcontainer`の保護は無効にできません。管理用の`/run/host/generated`と`/run/host/nix-env`もread-onlyで、`/workspace/.devcontainer`経由の書き込みも防ぎます。これらの生成物はホストが書きます。`.devcontainer/claude`自体の開発にはcloneを使います。

`git.shared_hidden_paths`で指定したパスと、リポジトリ内の`sumi.secrets`を空のread-onlyマウントで覆います。パスのリストの既定は空で、ディレクトリ名による自動選択はしません。対象は設定で指定します（[設定例](usage.md#gitの共有と分離)）。ディレクトリはtmpfs、ファイルは空ファイルのbindです。対象や親のsymlink、および保護を上書きする追加マウントは起動前に拒否します。対象は生成時に存在するパスに限られ、起動後に新しく作った未保護の場所までは隠せません。別名のコピーやハードリンク、Git履歴に入った秘密もこのマウントでは隠せません。

ホストのGit管理情報をagentから保護することで、ホストの`git diff`で変更をレビューできます。ただし、`flake.nix`、`package.json`のscripts、テストなど、ホストで実行する作業ツリーのファイルは編集できます。既存のGit設定が作業ツリー内の別の設定や実行ファイルを参照している場合も、その参照先はこの保護の対象外です。ignoredファイルは通常のstatusに出ないため、ホストで`git status --ignored`も確認してください。proxy、firewall、認証情報の代理注入、ホストDocker socketを渡さない構成は維持します。cloneへ戻すだけで共有中の変更の信頼性が回復するわけではありません。

## 保護の対象外

通信制限が適用されるのはコンテナ内で実行するプログラムです。取り込んだソースコードをホスト上で実行しても、この制限では保護できません。ホストのDocker操作権限は強い権限を持つため、ホストで使うスクリプトや起動設定はレビューしてから実行してください。

コミットに含まれるファイルはagentへ渡ります。cloneでは未コミット・未追跡ファイルは転送しませんが、Gitに入れた秘密の読取りは防げません。ホストのホーム、Docker socket、秘密のディレクトリを追加mountしないでください。Claudeが受け取る入力元の制限も対象外です。

VS Codeでコンテナに接続すると、VS Codeとその拡張機能が、proxyとfirewallを通らずにホストへ通じる経路をコンテナに作ります。接続している間は、コンテナで動くプロセスが、ホストでコマンドを実行し、proxyを通らずに外部と通信できます。この経路は遮断できないので、agentが動いているコンテナにはVS Codeを接続しないでください。[VS Codeで開く](usage.md#vs-codeで開く)を参照してください。

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

[proxy.hosts."mirror.my-company.com"]          # 平文HTTPのホスト
routes = ["GET /debian/.+"]
http = true
```

- `routes`には、通す要求を`GET /path`か、pathだけ（すべてのmethod）で書きます。pathはqueryを除いたURLのpath全体に一致させる正規表現です。`/.*`のように広く書くと、そのホストのどのAPIにも届きます。
- ホスト名は小文字のDNS名だけです。ワイルドカード、IPアドレス、port番号は書けません。portは443、HTTPSに限ります。ただし`http = true`を書いたホストは、port 80の平文HTTPでも同じ`routes`を通します（下記）。
- `headers`には、agentが送ってよいheaderを書きます。`Accept`や`Content-Type`などの一般的なものは書かなくても通ります。`Authorization`や`Cookie`などの認証は書けません。agentが付けた認証は常に除きます。
- `inject`には、proxyが付けるheaderを書きます。agentが付けた同名のheaderは置き換えます。`${NAME}`は秘密の参照です。値を`{ basic = "user:password" }`にするとBasic認証になります（テンプレートのGitHubの設定を参照）。`{ … }`のインラインテーブルは1行に書く必要があります。headerが多い場合は、`[proxy.hosts."api.notion.com".inject]`のテーブルに分けて書けます。
- `http = true`は、HTTPSに加えてport 80の平文HTTP（`http://ホスト名/...`）も通します。agentはHTTPSのときと同じく`HTTP_PROXY`でproxyを通ります。平文の要求と応答は経路上で読まれ、書き換えられえます。秘密が平文で流れないよう、`http = true`のホストには`inject`を書けません。
- `env`には、agentにダミー値`managed-by-proxy`で渡す環境変数の名前を書きます（下記）。
- `api.anthropic.com`と`mcp-proxy.anthropic.com`は書けません。proxyがAnthropicの認証値を付ける接続先で、ルールは`addon.py`に固定しています。

秘密の値は、ホストの`~/.config/claude-devcontainer/credentials.toml`の`[secrets]`に、値か、値を出力するコマンドで書きます。

```toml
[secrets]
NOTION_API_KEY = { command = "secret-tool lookup service notion" }
REDMINE_API_KEY = { command = "pass work/redmine" }

# ワークスペースのルートが一致するときだけ、名前ごとに上書きする
[workspaces."~/src/acme-*".secrets]
REDMINE_API_KEY = { command = "pass acme/redmine" }
```

注入スクリプトは、`proxy.hosts`が参照する秘密の名前を起動したproxyに問い合わせ（起動時の自動注入では`.generated/proxy.json`から求め）、その名前の値だけを求めて渡します。コマンドを実行するのもその名前のものだけです。ホストの環境変数は渡しません。`proxy.hosts`が参照する秘密がこのファイルにない場合や、コマンドが失敗した場合、proxyはその接続先への要求を、足りない秘密の名前を示して拒否します。

`[workspaces."パス"]`は、ワークスペースのルートのパスに一致したときに使う値です。パスは絶対パスか`~/`で始め、`*`と`?`はシェルのglobと同じく使えます（`*`は`/`にも一致します）。一致したものが書いた順に、`[secrets]`を名前ごとに、`[claude]`を丸ごと上書きします。リポジトリごとに別のPATを使い分けられます。

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

`claude.local.toml`のホストは`claude.toml`のホストに追加されます。同じホストが両方にある場合は、`claude.local.toml`の設定でホストごと置き換えます。`routes`、`headers`、`inject`、`http`、`env`は混ぜません。秘密は`claude.toml`と同じく、ホストの`~/.config/claude-devcontainer/credentials.toml`に書きます。

`claude.toml`と同じくimageのビルド時に組み込みます。編集したら、環境を停止して`devcontainer up`で起動し直してください。不正な場合は起動処理が失敗し、`claude.local.toml:`で始まる理由を表示します。ホストの未追跡ファイルなのでcloneには入りません。秘密の値はこのファイルに書かず、`${NAME}`で参照してください。

### 社内CAを信頼させる

社内CAの証明書を使うホストへは、proxyが接続先の証明書を検証できず、502を返します。ホストにあるCA証明書（PEM、複数の証明書を連結したものも可）のパスを、`claude.toml`か`claude.local.toml`の`[proxy]`の`ca`に書いてください。相対パスは`.devcontainer/`が基準です。

```toml
[proxy]
ca = "/etc/ssl/certs/company-root.pem"
```

proxyは起動時にこのファイルを読み取り専用でmountし、公開のルート証明書に加えて信頼します。CA証明書でないものが含まれていると、proxyは理由を表示して起動しません。imageには組み込まないので、再ビルドは要りません。ファイルを変えたら、環境を停止して`devcontainer up`で起動し直してください。

信頼はホストごとに分けられず、すべての接続先に及びます。このCAの秘密鍵を持つ者は`api.github.com`などの証明書も偽造でき、proxyとの間で認証値を盗み見られます。信頼できる組織のCAだけを指定してください。

### WebSearchを使う

`.devcontainer/claude.toml`またはホスト専用の`.devcontainer/claude.local.toml`に次を書きます。既定は`false`です。

```toml
[proxy]
web_search = true
```

READMEの停止手順で環境を停止し、`devcontainer up`で起動し直してください。設定はホストがproxyの環境変数へ渡します。agent内のファイルを編集しても、稼働中のproxyには反映されません。初めてこの機能を取り込むときはproxyのimageも更新されますが、その後の設定の切替えだけではimageの再ビルドは要りません。

許可するのはClaude Codeが使う`web_search_20250305`です。コード実行を伴う新しい版や未知の版は、この設定では許可しません。Claude Code側や組織の設定でWebSearchを禁止している場合は、その制限も受けます。

検索はAnthropicのサーバーで実行されるため、検索に伴う外部通信は`proxy.hosts`の制限を受けません。検索語や結果の取得元をこのproxyで審査することもできません。BashなどからMessages APIを直接呼ぶ場合にも同じ許可が適用されます。

auto modeの検索語の審査や、ツール結果へのprompt injection検知・警告は予防策として使えますが、proxyによる取得元の制限ではありません。WebFetch、code execution、MCP connector、URLやファイルIDで指定するcontentの許可は、この設定では増えません。

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

### unsafe_sharedで共有されるもの

`git.isolation = "unsafe_shared"`は作業ツリーだけでなくホストのindex・refs・objectsをRW共有します。agentのcommitやindexフラグ操作はホストにも直接反映され、Git状態の隔離はありません。`.devcontainer`、秘密マスク、Git設定・hooksなどの保護は維持します。詳細と制限は[利用方法](usage.md#gitも直接共有する-unsafe_shared)を参照してください。
