# 確認した範囲と検証手順

## 対応環境とバージョン

動作確認に使ったのはホストのEngine 29.6.2とコンテナ内のDinD 29.7.2です。Docker Desktopと他のOSでは確認していません。

## 確認した動作

外部サービスの代わりにローカルの模擬HTTPS APIを使い、実際のmitmproxyとDev Containers CLIによる起動・実行で検証しました。

| 対象 | 確認内容 |
|---|---|
| Dev Containersの起動・実行 | 認証用環境変数がない状態でのbuild/up、起動処理とClaude Codeの設定のコピーの完了を待ってからの接続、権限を落としたUID・GIDでの実行、認証なし403、注入コマンドでOAuth/API keyを渡した後の通信、通常のCLIからの新規プロセス実行 |
| proxyの認証処理 | 認証値の上書き、Cookieなど許可外のheaderの除去、認証値の更新と削除 |
| proxyの通信制限 | raw TCP、SOCKS、平文HTTP、許可外の接続先へのCONNECT、不正なTLS証明書の拒否 |
| Anthropic向け要求 | chunked本文・過大本文の早期切断、MCP・server tool・URL sourceの拒否、SSEの逐次配送 |
| Git向け応答 | 9 MiBの応答を逐次配送 |
| ネットワーク隔離 | agentとDinDからの外部IP・DNS・IPv6・ホストへの接続拒否。DinD内ではbridgeとhost networkの両方を確認 |
| proxy停止時 | 外部へ直接接続せず、通信が失敗すること |
| オフラインDocker | `images.txt`に書いたイメージの読み込み、build、実行 |
| Docker APIと公開TCPポート | agentのシェル、agent再起動後、実Claude Code 2.1.295のBash toolからの接続 |
| Testcontainers 12.1.0 | 起動、HTTP wait、mapped port、exec、Ryukによる残存コンテナの削除 |

## 未検証の範囲

実GitHubの送信先認可、実Anthropicの認証とAPI互換性、VS CodeのGUI接続は未検証です。

GitHubの書込み制限を実サービスで検証するには、専用の許可リポジトリ・非許可リポジトリと限定PATが必要です。RESTとGraphQLで非許可リポジトリへの書込みが拒否されることを確認するまでは、模擬APIの結果をGitHubの認可の実測とは扱いません。以下のテストはissue作成や既存リポジトリの変更を行いません。

利用条件と保護の対象外は、[通信ルールと隔離の範囲](network-policy.md)を確認してください。

## テストを実行する

すべてホストのターミナルで、このリポジトリのルートから実行してください。テストは既存のホストアカウントを読み取らず、合成した認証値を使います。

テストはランダムなComposeプロジェクト名でDockerリソースを作り、終了時にそのプロジェクトだけを削除します。subnetが重ならないよう、通常利用中の本環境は先に停止するか、`CLAUDE_DEVCONTAINER_PROXY_NET`と`CLAUDE_DEVCONTAINER_DIND_NET`を環境変数で別の値にしてください。`CLAUDE_DEVCONTAINER_UID`と`CLAUDE_DEVCONTAINER_GID`も同様に指定でき、その場合は下のagent imageも同じ値の`--build-arg AGENT_UID=… --build-arg AGENT_GID=…`でビルドしてください。

`AGENT_TEST_IMAGE`と`PROXY_TEST_IMAGE`でテスト用imageのタグを変更できます。全体試験には実際の`devcontainer`コマンドが必要です。別のCLI起動方法を使う場合は、`DEVCONTAINER_TEST_CLI`にコマンドを指定してください。テストは作業ツリーを一時領域へコピーし、CLIのuser-data-folderも専用領域に置きます。Dockerを使う試験を明示的に選んだのにdaemonが使えない場合はエラーになります。

### 準備

```sh
docker build -t claude-devcontainer-proxy:task1 .devcontainer/claude/proxy
docker build -t claude-devcontainer-agent:task2 --build-context workspace-config=.devcontainer \
  -f .devcontainer/claude/agent/Dockerfile .devcontainer/claude
# 実 Testcontainers 12.1.0 の依存を準備
sdk_dir=$(mktemp -d /tmp/cdc-sdk.XXXXXX)
cp tests/fixtures/package.json tests/fixtures/package-lock.json "$sdk_dir/"
npm ci --ignore-scripts --prefix "$sdk_dir"
docker pull busybox:1.37
docker pull testcontainers/ryuk:0.14.0
```

### 全体を検証する

```sh
# proxy 単体試験: 実 mitmproxy の flow を使用、外部接続なし
docker run --rm --network none -v "$PWD/.devcontainer/claude/proxy:/tests:ro" -w /tests \
  --entrypoint python claude-devcontainer-proxy:task1 \
  -m unittest discover -s . -p '*_test.py' -v
# 上の単体試験を除く全カテゴリ
RUN_DOCKER_TESTS=1 TESTCONTAINERS_NODE_MODULES="$sdk_dir/node_modules" \
  python3 -m unittest discover -s tests -p '*_test.py' -v
git diff --check
```

### 個別に検証する場合

特定の試験だけを実行する場合は、全カテゴリのコマンドに代えて、必要なものを選んで実行してください。

```sh
# 実 mitmproxy／模擬 TLS upstream
RUN_DOCKER_TESTS=1 python3 -m unittest discover -s tests -p proxy_integration_test.py -v
# 実 Dev Containers CLI up/exec・再起動・実 Claude Bash・SDK/Ryuk
RUN_DOCKER_TESTS=1 TESTCONTAINERS_NODE_MODULES="$sdk_dir/node_modules" \
  python3 -m unittest discover -s tests -p devcontainer_integration_test.py -v
```

### DinDとnixのフラグを検証する

Dev Containersの全体試験は、試験スクリプトがDinDを有効にして実行します。実行時は`CLAUDE_DEVCONTAINER_NIX=0`にしてください。Bashの起動試験は、nix有効・無効の両方で、非対話・対話・login shellと、VS Codeと同じくユーザーの`.bashrc`だけを読む経路を確認します。

実際のnixのdev shellとフラグの切替えは、以下の手順で手動確認してください。ホストにnixが必要です。

検証用のリポジトリを用意してください。コアをコピーするときは`cp -a`などでsymlinkを保持してください。`features/nix-1`のリンク先をコピーすると、ホストの`/nix/store`全体がコピーされてしまいます。

検証用リポジトリには、`hello`を含むdefault dev shellと、別のツールを含む名前付きdev shellを用意し、`flake.nix`と`flake.lock`をコミットしてください。設定値は`.devcontainer/.env`に書いてください。同名の環境変数があるとそちらが優先されるので、先に解除してください。

CLIとComposeで同じ検証用環境を操作するため、開始時に`export COMPOSE_PROJECT_NAME="cdc-flags-$(date +%s)"`も実行してください。ネットワークの範囲は、他の環境と重ならない値に設定してください。

1. `CLAUDE_DEVCONTAINER_DIND=0`、`CLAUDE_DEVCONTAINER_NIX=1`で`devcontainer up`を実行してください。`images.txt`には存在しないイメージ名を入れ、DinDが無効な場合に読み込みを待たず起動できることも確認してください。
2. 次のコマンドで、dev shellのツールが使え、Dockerには接続できず、dind-bridgeが起動していないことを確認してください。

   ```sh
   devcontainer exec --workspace-folder . bash -ic 'hello --version'
   devcontainer exec --workspace-folder . sh -c '! docker info >/dev/null 2>&1 && test ! -e /run/dind-bridge/serve.log'
   docker compose -f .devcontainer/compose.yaml ps --services --status running
   ```

   起動中のサービスは`agent`と`proxy`だけになります。

3. `flake.nix`のツールを変更し、コミットせずにもう一度`up`してください。コミット済みのdev shellが使われ、未コミットの変更が反映されないことを確認してください。
4. `CLAUDE_DEVCONTAINER_NIX_SHELL`に名前付きdev shellのattribute（例: `devShells.x86_64-linux.foo`）を指定して`up`し、そのshellのツールが使えることを確認してください。
5. `CLAUDE_DEVCONTAINER_NIX_REF`に存在しないrefを指定して`up`してください。警告が出て、`.devcontainer/.nix-env/env.rc`が更新されないことを確認してください。
6. `CLAUDE_DEVCONTAINER_NIX_REF`を有効なrefに戻してください。`flake.lock`がないコミットでも`up`し、作業ツリーへ`flake.lock`が作られていないことを確認してください。
7. `CLAUDE_DEVCONTAINER_NIX=0`で再起動し、dev shellのツールが読み込まれないことを確認してください。

設定を変えるときは、先に`docker compose -f .devcontainer/compose.yaml down`で検証用環境を停止してください。DinDを有効に戻す前には、検証用の存在しないイメージ名を`images.txt`から削除してください。

### 後片付け

検証が終わったら、準備に使ったホストのターミナルで一時ディレクトリを削除してください。

```sh
rm -rf "$sdk_dir"
```

## 検証結果の読み方

SDK依存を指定しなかった場合は、実Testcontainersの試験だけがskippedになります。

実Claudeの試験スクリプトは、agentのloopback上で模擬SSE APIを動かし、ダミーのAPI keyと新規の設定ディレクトリを使い、`--bare`でClaudeにBash toolを実行させます。ホストの認証状態は参照しません。CLIの終了コードだけでなく、`tool_result.is_error`、成功marker、Docker ping、公開TCPポートへの接続を確認します。

模擬HTTPS APIへの接続先の差替えは、production policyが宛先を検査した後にテスト用のaddonが行います。本番のproxyには接続先を差し替えるスイッチを追加していません。

[READMEに戻る](../README.md)
