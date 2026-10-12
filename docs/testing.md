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
