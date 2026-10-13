# テストと動作確認の結果

2026-10-12にagentの中（DinD）で行った、デモアプリのテストとcompose stackの確認結果です。

## テスト

| 対象 | 結果 | 内容 |
| --- | --- | --- |
| frontend | 1 file, 1 test passed | [frontend/test/containers.test.js](../frontend/test/containers.test.js)。busyboxコンテナのHTTP応答（`dind-ok`）を確認。テスト本体は549ms、vitest全体は812ms |
| backend | BUILD SUCCESSFUL。1 test, 0 failures, 0 errors, 0 skipped | [MessageRepositoryTest.java](../backend/src/test/java/demo/MessageRepositoryTest.java)。`readsTheSeededMessage`が2.071秒で成功。内訳は Ryuk の起動約0.46秒、postgres の起動約1.06秒 |

frontendの実行時に`node_modules are out of sync with your lockfile`のWARNが出ましたが、テストは通りました。`pnpm-workspace.yaml`の`verifyDepsBeforeRun: warn`による表示です。

テストは各1件で、検証範囲は限られます。frontendはReactのコンポーネントを、backendはJavalinのルートとMain.javaを検証していません。

## 実行環境

- DinDのDockerは29.7.2で、`tcp://127.0.0.1:2375`で接続されました。
- DinDには次のイメージが読み込まれていて、テスト中にpullは発生しませんでした。`busybox:1.37`、`eclipse-temurin:17-jre-alpine`、`nginx:1.27-alpine`、`postgres:16-alpine`、`testcontainers/ryuk:0.14.0`。
- Ryukは`testcontainers.properties`の指定どおり0.14.0が使われました。

## compose stack

- `pnpm -C frontend build`と`./gradlew --offline installDist`は成功しました。
- `docker compose up -d --build`で、db、backend、frontendが起動しました。dbとbackendはhealthyになりました。
- `curl http://127.0.0.1:3000/api/message`は`{"message":"hello from postgres"}`を返しました。`http://127.0.0.1:3000/`は200を返しました。

## 画面のスクリーンショット

- `http://127.0.0.1:3000/`の表示は、見出し「claude-devcontainer demo」と本文「backend says: hello from postgres」でした。
- 保存したスクリーンショットは[demo-compose.png](../.local/tmp/claude-1000/-workspace/40e692b9-cf2a-4dc8-a924-f66ca82d2558/scratchpad/demo-compose.png)です。
- 最初に待たずに撮った画像は、描画前の状態でした。後で待ってから撮り直し、同じファイル名で上書きしました。

## 停止

- `docker compose down`で、frontend、backend、dbのコンテナと`workspace_default`ネットワークが削除されました。
- 停止後、`docker ps -a`にコンテナは残っていませんでした。

## VS Codeのリモート拡張機能からホストでコマンドを実行

2026-10-14に、[VS Codeが作る経路](usage.md#vs-codeが作る経路)のうち、リモート拡張機能からホスト側のターミナルを開き、コマンドを実行する経路を実機で確認しました。

### 条件と手順

- ホストはLinux（NixOS）、VS Code 1.119.0（commit `8b640eef5a6c6089c029249d48efa5c99adf7d51`）、Dev Containers 0.469.0です。Windowsでは未検証です。
- 稼働中のagentコンテナへ「Attach to Running Container」に相当する接続を使いました。拡張機能から見た`vscode.env.remoteName`は`attached-container`でした。
- ホストのuser-dataとローカル拡張機能のディレクトリは、検証専用の一時ディレクトリに分けました。Workspace Trustはこの検証用設定で無効にしました。Restricted Modeでの検証ではありません。
- 検証用拡張機能をコンテナの`node`（UID 1000）でインストールしました。`extensionKind: ["workspace"]`、`activationEvents: ["onStartupFinished"]`を指定し、`~/.vscode-server/extensions/local-security-test.host-terminal-probe-0.0.1`から読み込みました。通常の接続で実行し、成功した試行では`--extensionDevelopmentPath`を使っていません。
- 拡張機能は一度だけ動作し、`workbench.action.terminal.newLocal`を呼びました。新しいターミナルがactiveになったことを確認してから、`workbench.action.terminal.sendSequence`へPythonコマンドと改行を渡しました。ホスト側のシェルは`bash --noprofile --norc`です。
- コマンドの処理は、専用の`/tmp/vscode-host-terminal-probe-5cba7a2b2ce2/host-result.json`へランダムな識別子と実行環境を書き出すだけです。このディレクトリはコンテナへマウントしていません。

### 結果

| 観測点 | 結果 |
| --- | --- |
| リモート拡張機能の実行場所 | hostname `2ff361e5c7d4`、UID 1000、`/.dockerenv`あり |
| 拡張機能による操作 | `new-local-terminal`、`sequence-sent`を順に記録 |
| ホストに作られたファイル | hostname `nixos`、UID 1000、cwd `/home/hogeyama`、`/.dockerenv`なし |
| ファイルに記録されたmount namespace | `mnt:[4026531832]`。ホストから別途読んだ値と一致 |
| コンテナのmount namespace | `mnt:[4026533492]`。ホストと異なる |
| ランダムな識別子 | 拡張機能のコマンドに埋め込んだ値とファイル内の値が一致 |
| コンテナから同じパスを確認 | ファイルは存在しない |

共有ファイルへの直接書き込みではなく、コンテナ内の拡張機能からVS Codeを通じてホストのユーザー権限でPythonを実行できました。コマンド送信後に、人間がターミナルでEnterを押す操作は行っていません。

この試行で確認したのは、`node`権限で配置した検証用拡張機能からの実行です。既存の第三者拡張機能の書き換え、Restricted Modeの回避、Windows上での実行は試していません。
