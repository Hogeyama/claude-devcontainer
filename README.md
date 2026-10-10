# Claude Code用Dev Container

Claude Code用のDev Containerです。危険な操作を禁止しつつ、DinDなどの機能をエージェントに提供します。

## ホストに必要なもの

- Linux x86_64
- Docker EngineとDocker Compose、およびDockerを操作できる権限
- Dev Containers CLI（`devcontainer`コマンド）
- Python 3
- privilegedコンテナ、ユーザー・ネットワークnamespace、bubblewrapを利用できるカーネル
- `10.231.70.0/24`と`10.231.71.0/24`がホスト・VPN・他コンテナと重複せず、ホストのfirewallがコンテナ間通信を許可する環境

## 利用上の制限

* 安全に利用するために、読み取り権限のみのGitHub fine-grained tokenを利用することを前提にしています。
* package registry、GitHubのrelease assetやLFS、Anthropicのweb searchやMCP connectorなどは使えません。
  * [通信ルールと隔離の範囲](docs/network-policy.md)に対応範囲を記載しています。
* UID 1000で読み書きできるリポジトリでしか使えません（今のところ）。

## クイックスタート

### 1. 認証情報を用意する

#### GitHub

GitHubで[fine-grained PATを発行](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-fine-grained-personal-access-token)します。resource ownerを指定し、共有を許可した非公開リポジトリだけを対象にしてください。Contents、Issues、Pull requestsなど、作業に必要な権限だけを付けます。

#### Claude

Claudeのサブスクリプションを使う場合は、ホストのClaude Codeで[`setup-token`](https://code.claude.com/docs/en/cli-reference)を実行し、対話手順に従って長期トークンを発行します。

```sh
claude setup-token
```

API keyを使う場合は、代わりに[Claude ConsoleのAPI keys](https://platform.claude.com/settings/keys)で発行したkeyを使います。

### 2. 認証情報の取り出し方を設定する

ホストの`~/.config/claude-devcontainer/credentials`に、認証情報を取り出すシェルの代入を書きます。`GITHUB_TOKEN`と、`CLAUDE_CODE_OAUTH_TOKEN`または`ANTHROPIC_API_KEY`のどちらか一方を設定します。取り出し方は手元の環境に合わせて選んでください。

```sh
GITHUB_TOKEN=$(pass github/token/for-agent)                       # passを使う場合
CLAUDE_CODE_OAUTH_TOKEN=$(secret-tool lookup service claude-code) # libsecretを使う場合
# ANTHROPIC_API_KEY=$(op read op://Private/anthropic/credential)  # 1Password CLIを使う場合
# GITHUB_TOKEN=github_pat_...                                     # 値を直接書く場合。ファイルはchmod 600にしてください
```

このファイルはホストで実行されます。agentが書き換えられる作業ツリーの中には置かないでください。

### 3. 起動する

```sh
devcontainer up
```

Dev Containersがimageのビルドと3つのコンテナ（本体、DinD、プロキシ）の起動を行います。
プロキシには2.で設定した認証情報が設定されます。

### 4. 実行する

CLIとして実行する場合:

```sh
devcontainer exec claude
```

VS Codeで実行する場合:

1. ホストのVS CodeにDev Containers拡張をインストールします。
2. このリポジトリで`code .`を実行し、通知またはコマンドパレットから「Dev Containers: Reopen in Container」を実行します。上の手順で起動したコンテナにそのまま接続します。

VS Codeはホストの認証情報をコンテナへ転送する機能を持っています。使う前に[VS Codeで開く](docs/usage.md#vs-codeで開く)の利用条件を確認してください。

> [!NOTE]
> Claudeが接続できない場合は、[接続できない場合](docs/troubleshooting.md)を確認してください。

> [!NOTE]
> コマンドパレットから「Dev Containers: Install devcontainer CLI」を実行してCLIを落とすと、以降は `devcontainer open .` で直接 Dev Container を使った VS Code を開くことができます。
> npmやGitHubリリース経由でインストールできるdevcontainer CLIにはこの機能（`open`サブコマンド）がありません。

## 5. 終了する

ClaudeはCtrl-Cか`/exit`で終了します。VS Codeで開いていた場合は、ウィンドウを閉じるとComposeも停止します。

CLIで使っていた場合は、ホストのこのリポジトリのルートで次を実行してComposeを停止します。

```sh
project=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' \
  "$(docker ps -q --filter "label=devcontainer.local_folder=$(pwd -P)" --filter "label=com.docker.compose.service=agent")")
docker compose -p "$project" -f .devcontainer/compose.yaml down
```

作業ファイル、DinDのデータ、プロキシのCAは残ります。これらも消す場合は`down --volumes`を使います。
