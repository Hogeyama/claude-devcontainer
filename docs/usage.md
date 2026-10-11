# 追加の利用手順

基本の起動・終了手順は[README](../README.md)を参照してください。この文書のホスト向けコマンドは、取り込んだリポジトリのルートで実行してください。認証情報は、ホストのClaude Codeのログインと`~/.config/claude-devcontainer/credentials`から読みます。

## 認証情報を更新する

Claudeの認証には、ホストのClaude Codeのログインを使います。ホストで`claude`にログインしていれば、設定は要りません。ログインし直した場合や、`~/.config/claude-devcontainer/credentials`で`proxy.hosts`が参照する秘密（`GITHUB_TOKEN`など）を変えた場合は、注入スクリプトを再実行してください。proxyやagentの再起動・再ビルドは不要です。

```sh
python3 .devcontainer/claude/scripts/inject-credentials.py
```

proxyは1環境につき1組の認証値を持ち、次の要求から新しい値を使います。認証値はproxy専用tmpfsに保存され、置き換えるかproxyを停止するまで有効です。ファイルはproxyのUID 1000（agentのUIDの設定とは無関係）が所有する0400で、agentやDinDからは読めません。

`devcontainer up`で起動すると、注入スクリプトは環境が動いている間ホストで動き続けます。proxyが作り直されれば、その都度注入し直します。

### ホストのClaude Codeのログイン

注入スクリプトは、ホストのClaude Codeのログイン情報（`$CLAUDE_CONFIG_DIR`、なければ`~/.claude`の`.credentials.json`）からaccess tokenを読み、proxyへ渡します。別の場所のファイルを使う場合は、`~/.config/claude-devcontainer/credentials`に`CLAUDE_CREDENTIALS_FILE=<パス>`と書いてください。

- agentには本物のトークンを見せません。
- 期限の5分前になると、ホストからAnthropicへ直接、トークンの更新を要求し、新しいトークンをファイルへ書き戻してからproxyへ渡し直します。ホストのClaude Codeと同じロックを取ってからファイルを読み直すので、ホストのClaude Codeと同時に更新して壊すことはありません。ホストのClaude Codeが先に更新した場合は、その値を使います。
- 書き戻すのは`claudeAiOauth`のaccess token、refresh token、期限だけです。
- 更新に失敗した場合は、今のトークンを使い続け、30秒後にやり直します。ホストで`claude /login`し直せば、新しいログインが使われます。
- agentのClaude Codeには、ログインのscope、subscriptionの種類などを`/etc/claude-code/managed-settings.d/60-claude-login.json`で渡します。トークンは含みません。
- 更新の要求の形式は、Claude Code 2.1.295の実装に合わせています。Claude Codeがこれを変えると、更新は失敗します。その場合も、ホストのClaude Codeを使っていれば、その更新した値を読み直して動き続けます。

`claude setup-token`のトークン（`CLAUDE_CODE_OAUTH_TOKEN`）は使いません。推論用のscopeしか持たず、claude.aiのコネクタが使えないうえ、期限切れを自動で更新できないためです。設定ファイルに残っていると、注入スクリプトが警告を出して無視します。

### API keyを使う

ログインの代わりに、Claude ConsoleのAPI keyも使えます。`~/.config/claude-devcontainer/credentials`に`ANTHROPIC_API_KEY=…`と書いてください。API keyでは、Claude CodeがArtifactsとclaude.aiのコネクタを使えません。API keyは自動では更新しないので、Consoleで更新したら注入スクリプトを再実行してください。

注入コマンドは起動済みの環境だけを対象にします。環境がない場合や対象が複数ある場合は失敗するので、先に`devcontainer up`を実行してください。`proxy.hosts`が参照する秘密は、`~/.config/claude-devcontainer/credentials`で代入したものだけを渡し、環境変数からは取りません。Claudeの認証（`ANTHROPIC_API_KEY`か`CLAUDE_CREDENTIALS_FILE`）は環境変数でも指定でき、その場合はファイルの指定より優先します。

認証値はすべてのagentプロセスの通信に共通です。すでに開いているVS Codeのターミナルや通常の`devcontainer exec`からも同じ認証で通信でき、利用者ごとの分離はありません。agentのClaude Codeは起動時にダミーのOAuthトークンを使い、proxyが本物のトークンまたはAPI keyへ置き換えます。

## DockerとTestcontainers

DinDは`.devcontainer/claude.toml`の`[docker]`に`enabled = true`と書いた場合だけ起動します。作業用コンテナから使うDockerは、専用のDinDコンテナで動作します。registry、外部DNS、ホストサービスには接続できません。必要なイメージは、同じ`[docker]`の`images`に`名前:tag`の形で並べてください。

```toml
[docker]
enabled = true
images = ["testcontainers/ryuk:0.14.0", "postgres:16"]
```

`devcontainer up`のたびに、イメージ読み込みスクリプトがホストにないイメージをホストでpullし、DinDへ読み込みます。

- イメージ読み込みスクリプトは、ホストにすでにあるイメージをtagが動いていてもpullし直しません。新しくしたいときは、ホストで`docker pull`してから`up`してください。
- DinDのデータはvolumeに残るので、送るのは初回と、ホストのイメージが変わったときだけです。
- コンテナの起動時は、読み込みが終わるまで`up`が待ちます。失敗したイメージは警告に出ます。詳しくは`~/.local/state/claude-devcontainer/load-images.log`を見てください。
- `up`の後に`images`を変えた場合は、ホストで`.devcontainer/claude/scripts/load-images.py`を実行すると反映できます。

イメージ読み込みスクリプトは、`images`にある名前をホストでそのままpullするので、`claude.toml`は`.devcontainer/`に置いてagentから書き換えられないようにしています。agentが名前を選べると、ホストを任意のregistryへ接続させられるからです。agentのコミットで`claude.toml`が変わっていないかも確認してください。

投入後は、agentのシェルから`docker run`や`docker build --network=none`を実行できます。buildには投入済みのbase imageとローカルにある依存ファイルを使ってください。Gradleは`--offline`で実行し、その他のツールも事前に用意したキャッシュや依存ファイルを使ってください。ビルド中のオンラインpackage installには対応していません。

agentにツールを追加する場合は、[`.devcontainer/agent-setup.sh`](../.devcontainer/agent-setup.sh)に導入手順を書いてください。ホストでagentのimageをビルドする際にrootで実行されます。

コンテナのTCPポートは、`docker run -p 8080`または`-p 18080:8080`のように、既定の`0.0.0.0`で公開してください。agentからは、`127.0.0.1:公開ポート`に接続できます。loopbackだけで公開したポートは中継されません。Docker API用の2375番ポートも公開先としては使えません。

Testcontainersでは、テスト対象とRyukのイメージを事前に投入してください。以下の接続設定はComposeで設定済みです。

```sh
DOCKER_HOST=tcp://127.0.0.1:2375
TESTCONTAINERS_HOST_OVERRIDE=127.0.0.1
TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/run/user/1000/docker.sock  # 1000はagentのUID
```

## VS Codeで開く

VS CodeのGUI接続と、ホスト認証が転送されないことは未検証です。Dev Containersには[ホストのGit認証を共有する機能](https://code.visualstudio.com/remote/advancedcontainers/sharing-git-credentials)があります。使う場合は認証情報のない専用ホストアカウントと専用editor profileを用意してください。SSH／GPG agentや、ホストのGitHub／Docker認証を用意・転送しないことが利用条件です。

認証用環境変数を渡さずに、専用profileのVS Codeを起動してこのリポジトリを開いてください。Dev Containersの「Reopen in Container」を実行すると、作業ディレクトリは`/workspace`になります。ホストのホームや認証用socketを追加mountしないでください。起動後、認証用環境変数を設定したホストの別ターミナルから`python3 .devcontainer/claude/scripts/inject-credentials.py`を1回実行し、認証値をproxyへ渡してください。その後はVS Codeのコンテナ内ターミナルで通常どおり`claude`を実行できます。

ビルド、テスト、デバッグやコードを実行する拡張機能はコンテナ内で動かしてください。改変されたGradle設定を人間が実行する場合も、コンテナ内であれば通信制限の対象です。ホストで実行するスクリプト、Dockerfile、起動設定は先にレビューしてください。

### 認証転送を抑制する設定

Dev Containers拡張機能0.469.0では、以下をホストのuser/profile設定に指定できます。

```json
{
  "dev.containers.copyGitConfig": false,
  "dev.containers.gitCredentialHelperConfigLocation": "none",
  "dev.containers.dockerCredentialHelper": false,
  "dev.containers.githubCLILoginWithToken": false
}
```

これらは認証helperの設置などを抑制する設定です。既存のsocketや拡張機能を通じた認証転送まで遮断できたという検証結果はありません。`remoteEnv`で環境変数を消すだけでも不十分です。

[READMEに戻る](../README.md)
