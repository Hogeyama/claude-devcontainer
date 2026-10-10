# 通信ルールと隔離の範囲

この環境は、作業用の`agent`、外部通信を検査する`proxy`、オフラインのDockerを提供する`dind`の3コンテナで構成します。agentから外部へ直接接続する経路はなく、通信はproxyを通ります。proxyを停止すると通信も失敗し、直通には切り替わりません。

## 許可される通信

| 接続先 | 許可する用途 |
|---|---|
| `api.github.com` | リポジトリ向けREST API、GraphQL、`GET /user` |
| `github.com` | Git smart HTTPによるclone、fetch、push |
| `api.anthropic.com` | Messages API、token count、Claude Codeの起動時の接続確認（`GET /api/hello`） |

GitHubの認証は、proxyに渡したfine-grained PATで上書きします。GraphQLの本文は解析せず、リポジトリへの書込みが許されるかはGitHubの認可に任せます。このため、PATは共有を許可した非公開リポジトリだけを対象にする必要があります。公開リポジトリの読取りは残ります。

GitHubのarchive、LFS、release assetや、package registryには対応していません。WebSocket、SOCKS、raw TCP、平文HTTPによる外部接続も拒否します。

Anthropicでは、Anthropicのサーバーから別の接続先へ通信させる指定だけを拒否します。対象はMCP connector、web search／fetchなどサーバー側で実行するtool、code executionのcontainer、URLやファイルIDで指定するcontentです。それ以外の項目やbeta headerは通すため、Claude Codeの新しい機能でproxyの更新は要りません。この4種類以外の形でサーバーから外部へ接続する機能が今後追加された場合は、それも通ります。

拒否した要求には通常HTTP 403を返します。本文付きの要求をheaderの段階で拒否した場合は、本文を受信し続けないよう接続を切断します。

## 認証とCA

本物のPATとAnthropic認証値、HTTPSの検査に使うCA秘密鍵はproxyだけが持ちます。認証値は起動後にホストの環境変数から注入コマンドの標準入力を通じて渡し、proxy専用tmpfsの0400ファイルに保存します。Dockerの作成設定やimageには含めません。認証値がない場合やファイルが不正な場合は要求を拒否し、以前の値には戻りません。agentにはダミーのAPI keyを環境変数で設定しています。proxyが外部へ接続する際に、本物の値へ置き換えます。

CA秘密鍵は専用volumeに保存し、agentには公開CAだけを渡します。公開CAはsystem trustに追加済みで、Node.js、Git、curl用の環境変数も設定しています。独自のtruststoreを使うツールでは、公開CAを別途追加してください。TLS検証を無効化しないでください。

## コンテナ内の実行権限

agentの起動処理はfirewallとCAを設定してからUID 1000へ切り替えます。通常のプロセスにはcapabilityを与えず、`no-new-privileges`を設定します。コンテナ内でBash sandboxを作れるよう、seccompとAppArmorの制限は緩和しています。

Claudeのmanaged settingsでは、Bash sandboxを有効にしています。sandboxを利用できない場合は失敗し、sandbox外でのコマンド実行は許可しません。設定値は`enabled`、`failIfUnavailable`、`allowUnsandboxedCommands=false`、`enableWeakerNestedSandbox=true`です。

rootless DinDはprivilegedコンテナで動かします。同梱の[`dind-bridge`](../.devcontainer/claude/dind-bridge/README.md)がDocker APIと公開TCPポートをagentへ中継します。

agentへmountするDocker APIのUnix socketはread-onlyですが、Docker APIの操作権限を読取り専用にはしません。ホストのDocker socketは共有しません。

## 保護の対象外

通信制限が適用されるのはコンテナ内で実行するプログラムです。共有したソースコードをホスト上で実行しても、この制限では保護できません。ホストのDocker操作権限は強い権限を持つため、ホストで使うスクリプトや起動設定はレビューしてから実行してください。

作業ツリー全体がagentへ共有されます。この環境は、共有ファイルに含まれる秘密の読取りを防ぐものではありません。ホストのホーム、Docker socket、秘密のディレクトリを追加mountしないでください。Claudeが受け取る入力元の制限も対象外です。

VS Codeから使う場合には、エディタを通じたホスト認証の転送にも注意が必要です。[VS Codeの利用条件](usage.md#vs-codeで開く)を参照してください。

## 通信ルールを変更する

ルールは[`proxy/addon.py`](../.devcontainer/claude/proxy/addon.py)に固定しています。稼働中のproxyへ作業ツリーはmountしないため、ファイルを編集するだけでは反映されません。

追加先の認証方法と送信先の制限をレビューしてから、READMEの停止手順で環境を停止し、ホストで取り込んだリポジトリのルートで次を実行します。

```sh
devcontainer build
devcontainer up
python3 .devcontainer/claude/scripts/inject-credentials.py
```

Anthropicの要求本文にはContent-Lengthが必要です。本文は8 MiB以下に制限し、全体を受信して検査します。Gitの要求本文には同じ上限を設けていないため、大きな要求や同時接続でメモリを使い切る可能性が残ります。proxyのメモリ上限は512 MiBで、停止した場合は通信も停止します。

[READMEに戻る](../README.md)
