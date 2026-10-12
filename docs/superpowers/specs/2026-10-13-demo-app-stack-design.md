# デモ拡充: pnpm + Gradle + DinD compose + playwright-cli

`.devcontainer/`直下と`flake.nix`は、このDev Containerを取り込んだリポジトリの例(デモ)として機能させる。このデモを拡充し、pnpmとJava (Gradle) でTestcontainersを使ったテスト、DinD上でのフロントエンド/バックエンド協調起動、playwright-cliでのスクリーンショットまでを一通り見せる。

## 背景と制約

- agentのコンテナはpackage registryに接続できない。依存はホストの`node_modules`と`~/.gradle`をCoW overlayで共有する
- DinDは外部のregistryに接続できない。イメージは`claude.toml`の`docker.images`に列挙し、`devcontainer up`でホストからDinDへ読み込む
- DinD内のコンテナはagentの`/workspace`をbind mountできない。アプリのイメージは`docker compose build`で作る(build contextはsocket経由でdaemonに送られるため、オフラインで動く)
- dind-bridgeは、`0.0.0.0`にpublishされたポートをagentの`127.0.0.1`の同じポートに中継する
- aptのGradleは4.4.1で実用にならない。Gradleはwrapper経由で、配布物はホストの`~/.gradle`から供給する
- pnpm workspaceは`pnpm-workspace.yaml`の`nodeLinker: hoisted`で依存がルートの`node_modules`にフラット配置される(検証済み)。これにより既存の`/workspace/node_modules` overlayだけでworkspace全体を賄える。`.npmrc`の`node-linker`はpnpm 11では効かないので`pnpm-workspace.yaml`に書く

## 構成

すべてコミット対象で、agentのcloneにも入る。

```
package.json            # private root(workspaceの置き場のみ)
pnpm-workspace.yaml     # packages: [frontend], nodeLinker: hoisted
frontend/               # React + Viteのpnpmパッケージ
  package.json          #   react, react-dom / vite, @vitejs/plugin-react, vitest, testcontainers
  vite.config.js, index.html, src/…
  test/containers.test.js   # vitest + testcontainers: busybox httpdを起動しfetchで検証
  Dockerfile            # FROM nginx:alpine, COPY dist + nginx.conf
  nginx.conf            # 静的配信 + /api/をbackend:8080へproxy
backend/                # JavalinのGradleプロジェクト(application plugin + installDist)
  gradlew, gradle/wrapper/…, settings.gradle, build.gradle
  src/main/java/…       # GET /api/messageがPostgresのメッセージを返す
                        # (起動時にCREATE TABLE + seed INSERT)
  src/test/java/…       # JUnit5 + Testcontainers: PostgreSQLContainerでrepositoryを検証
  src/test/resources/testcontainers.properties  # ryukを0.14.0に固定
  Dockerfile            # FROM eclipse-temurin:17-jre-alpine, COPY build/install/backend
compose.yaml            # DinD側のアプリスタック(db + backend + frontend)
```

### ルートcompose.yaml

- `db`: postgres:16-alpine、healthcheckは`pg_isready`
- `backend`: `build: ./backend`、JDBC接続先は環境変数、`depends_on: db: service_healthy`
- `frontend`: `build: ./frontend`、ports `3000:80`、`depends_on: backend: service_healthy`
  - dind-bridgeがagentの`127.0.0.1:3000`に中継するので、`localhost:3000`でスクショできる
- ホストのDockerでもそのまま`docker compose up`できる(デモとしての対称性)

### claude.toml

`docker.images`に`postgres:16-alpine`、`nginx:1.27-alpine`、`eclipse-temurin:17-jre-alpine`を追加する。`busybox:1.37`と`testcontainers/ryuk:0.14.0`は既存。npm側はtestcontainers 12.xを使い、既定のRyukが0.14.0なので固定の指定は不要(tests/fixturesと同じ)。Java側は既定が異なるため`testcontainers.properties`で0.14.0に固定する。

### flake.nix

devShellに`pnpm`、`jdk17_headless`(存在しなければ`jdk17`)、`gradle`を追加する。

- ホストが`nix develop`で依存をseedできる
- agentのBashも同じツールをPATH経由で使える
- aptのopenjdk-17とメジャーバージョンを揃えるので、agent内にJDKが2系統あっても衝突しない

### agent-setup.sh

aptの`gradle`行を削り`openjdk-17`のみにする。4.4.1ではプロジェクトをビルドできないため、残すとデモとして誤解を生む。

### .gitignore

`node_modules/`、`frontend/dist/`、`backend/build/`、`backend/.gradle/`を追加する。

### ドキュメント

README「このリポジトリを開発する」節に、デモの構成とseed手順を追記する。

## ワークフロー

1. ホスト: `pnpm install` → ルート`node_modules`に集約(overlayのlower)
2. ホスト: `cd backend && ./gradlew build` → `~/.gradle`に配布物と依存(overlayのlower)
3. agent: `pnpm -C frontend test` → vitestがDinD上にbusyboxを起動してfetch
4. agent: `cd backend && ./gradlew --offline test` → TestcontainersがDinD上にpostgresを起動
5. agent: `pnpm -C frontend build && ./gradlew installDist && docker compose up -d` → frontend/backend/dbが協調起動
6. agent: `playwright-cli screenshot http://localhost:3000 shot.png`

## 捨てた選択肢

- frontendをnode/vite previewコンテナで動かす: hoisted `node_modules`が`frontend/`の外にあるためbuild contextとして扱いにくい。nginx静的配信+proxyのほうが標準的でシンプル
- Spring Boot: 依存ツリーが大きくseedが重い。Javalin + Jackson + JDBCで依存のデモとしては十分
- apt Gradleの維持: 4.4.1ではwrapper配布物も含めビルドできない

## 検証

- ホストで`pnpm install`・`pnpm -C frontend build`・`pnpm -C frontend test`が動く
- ホストで`cd backend && ./gradlew build test`が動く(= agentのlowerがseedされる)
- ホストのDockerで`docker compose up`し、`localhost:3000`がフロントエンドを返し`/api/`がbackendに届く
- agent側の依存共有・オフラインGradleは既存のoverlay機構どおりに動く(起動中の環境で目視確認)
