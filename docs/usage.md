# 追加の利用手順

基本の起動・終了手順は[README](../README.md)を参照してください。この文書のホスト向けコマンドは、取り込んだリポジトリのルートで実行します。認証情報を渡すホストのターミナルには、READMEの認証用環境変数を設定してください。

## 認証情報を更新する

ホストの`GITHUB_TOKEN`と、`CLAUDE_CODE_OAUTH_TOKEN`または`ANTHROPIC_API_KEY`を更新し、`python3 .devcontainer/claude/scripts/inject-credentials.py`を再実行します。proxyやagentの再起動・再ビルドは不要です。

```sh
python3 .devcontainer/claude/scripts/inject-credentials.py
```

proxyは1環境につき1組の認証値を持ち、次の要求から新しい値を使います。認証値はproxy専用tmpfsに保存され、置き換えるかproxyを停止するまで有効です。ファイルはproxyのUID 1000（agentのUIDの設定とは無関係）が所有する0400で、agentやDinDからは読めません。proxyを再起動した場合は、再び同じコマンドで渡してください。

期限切れ時の自動更新は行いません。OAuthトークンは、READMEの手順に従ってホストの`claude setup-token`で再発行し、`CLAUDE_CODE_OAUTH_TOKEN`へ貼り付けてから`python3 .devcontainer/claude/scripts/inject-credentials.py`を再実行します。API keyを使う場合はConsoleで更新します。

注入コマンドは起動済みの環境だけを対象にします。環境がない場合や対象が複数ある場合は失敗するので、先に`devcontainer up`を実行してください。ホストの環境変数から読む認証値だけを受け付け、既存のホスト認証ファイルは読みません。

認証方式はproxyが選び、すでに開いているVS Codeのターミナルや通常の`devcontainer exec`からも同じ認証で通信できます。Claudeは起動時にダミーのAPI keyを使い、proxyが選択されたOAuthトークンまたはAPI keyへ置き換えます。認証値はすべてのagentプロセスの通信に共通なので、利用者ごとの分離はありません。

## DockerとTestcontainers

DinDは`.devcontainer/.env`に`CLAUDE_DEVCONTAINER_DIND=1`と書いた場合だけ起動します。作業用コンテナから使うDockerは、専用のDinDコンテナで動作します。registry、外部DNS、ホストサービスには接続できません。必要なイメージは`.devcontainer/images.txt`に1行に1つ`名前:tag`の形で書きます。`#`以降はコメントです。

```
testcontainers/ryuk:0.14.0
postgres:16
```

`devcontainer up`のたびに、ホストにないイメージをホストでpullし、DinDへ読み込みます。

- ホストにすでにあるイメージは、tagが動いていてもpullし直しません。新しくしたいときは、ホストで`docker pull`してから`up`してください。
- DinDのデータはvolumeに残るので、送るのは初回と、ホストのイメージが変わったときだけです。
- コンテナの起動時は、読み込みが終わるまで`up`が待ちます。失敗したイメージは警告に出ます。詳しくは`~/.local/state/claude-devcontainer/load-images.log`を見てください。
- `up`の後にリストを変えた場合は、ホストで`.devcontainer/claude/scripts/load-images.py`を実行すると反映できます。

ホストはリストにある名前をそのままpullするので、`images.txt`は`.devcontainer/`に置いてagentから書き換えられないようにしています。agentが名前を選べると、ホストを任意のregistryへ接続させられるからです。agentのコミットで`images.txt`が変わっていないかも確認してください。

投入後は、agentのシェルから`docker run`や`docker build --network=none`を実行できます。buildには投入済みのbase imageとローカルにある依存ファイルを使います。Gradleは`--offline`で実行し、その他のツールも事前に用意したキャッシュや依存ファイルを使ってください。ビルド中のオンラインpackage installには対応していません。

agentにツールを追加する場合は、[`.devcontainer/agent-setup.sh`](../.devcontainer/agent-setup.sh)に導入手順を書きます。ホストでagentのimageをビルドする際にrootで実行されます。

コンテナのTCPポートは、`docker run -p 8080`または`-p 18080:8080`のように、既定の`0.0.0.0`で公開します。agentからは、`127.0.0.1:公開ポート`に接続できます。loopbackだけで公開したポートは中継されません。Docker API用の2375番ポートも公開先としては使えません。

Testcontainersでは、テスト対象とRyukのイメージを事前に投入します。以下の接続設定はComposeで設定済みです。

```sh
DOCKER_HOST=tcp://127.0.0.1:2375
TESTCONTAINERS_HOST_OVERRIDE=127.0.0.1
TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/run/user/1000/docker.sock  # 1000はagentのUID
```

## VS Codeで開く

VS CodeのGUI接続と、ホスト認証が転送されないことは未検証です。Dev Containersには[ホストのGit認証を共有する機能](https://code.visualstudio.com/remote/advancedcontainers/sharing-git-credentials)があります。使う場合は認証情報のない専用ホストアカウントと専用editor profileを用意してください。SSH／GPG agentや、ホストのGitHub／Docker認証を用意・転送しないことが利用条件です。

認証用環境変数を渡さずに、専用profileのVS Codeを起動してこのリポジトリを開きます。Dev Containersの「Reopen in Container」を実行すると、作業ディレクトリは`/workspace`になります。ホストのホームや認証用socketを追加mountしないでください。起動後、認証用環境変数を設定したホストの別ターミナルから`python3 .devcontainer/claude/scripts/inject-credentials.py`を1回実行し、認証値をproxyへ渡します。その後はVS Codeのコンテナ内ターミナルで通常どおり`claude`を実行できます。

ビルド、テスト、デバッグやコードを実行する拡張機能はコンテナ内で動かします。改変されたGradle設定を人間が実行する場合も、コンテナ内であれば通信制限の対象です。ホストで実行するスクリプト、Dockerfile、起動設定は先にレビューしてください。

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
