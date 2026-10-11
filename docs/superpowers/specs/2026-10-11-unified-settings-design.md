# 設定ファイルを claude.toml に一本化する

## 目的

利用者が書く設定は現在、形式も場所もばらばらである。

| 設定 | 今の場所 | 形式 |
| --- | --- | --- |
| DinD・nix・sumi の有効化、UID、サブネット | `.devcontainer/claude.env`、`.devcontainer/.env`、環境変数 | env |
| 社内 CA、claude.ai のコネクタ | 同上 | env |
| 接続先と注入する認証 | `.devcontainer/proxy.yaml`、`.devcontainer/proxy.local.yaml` | YAML |
| DinD に読み込むイメージ | `.devcontainer/images.txt` | 1行1件 |
| sumi の秘密の一覧 | `~/.claude/sumi/secrets.txt`（場所は env の DIR と FILE の2項目で指定） | 1行1件 |
| 秘密の値 | `~/.config/claude-devcontainer/credentials` | シェルの代入 |
| エージェントへコピーするファイル | `~/.config/claude-devcontainer/extra/` | ディレクトリ |

1つの機能の設定が複数のファイルに分かれ（DinD は env とイメージの一覧、通信は proxy.yaml と env と credentials）、ホストだけの設定にも置き場所が3つある。原因は、compose が環境変数しか読めないことと、proxy のルールを image に組み込むことという実装の都合が、そのまま利用者の書く形になっていることである。

これを次の形にそろえる。

- リポジトリで共有する設定は `.devcontainer/claude.toml`、そのホストだけの設定は `.devcontainer/claude.local.toml` に書く。形式も項目も同じで、後者が前者を上書きする。
- ホスト全体で使う設定と秘密は `~/.config/claude-devcontainer/` に置く（`credentials`、`extra/`、`sumi/secrets.txt`）。
- `devcontainer.json`、`compose.yaml`、`agent-setup.sh` は Dev Container 標準の設定として今のまま残す。

compose 用の環境変数や proxy 用のルールは、`initializeCommand` が `claude.toml` から生成する。利用者はそれらを書かない。

まだ利用者がいないので、古いファイル（`claude.env`、`.env`、`proxy.yaml`、`proxy.local.yaml`、`images.txt`）との互換は持たない。

## 利用者から見た形

### ファイル

```
.devcontainer/
├── devcontainer.json
├── compose.yaml          環境変数・volume（今のまま）
├── agent-setup.sh        ツールの導入（今のまま）
├── claude.toml           共有する設定。コミットする
├── claude.local.toml     このホストだけの設定。コミットしない（任意）
├── .gitignore            claude.local.toml を除外する
├── .generated/           initializeCommand が生成する。編集しない（中の .gitignore で自身を除外する）
└── claude/               取り込んだもの
~/.config/claude-devcontainer/
├── credentials           秘密の値（今のまま）
├── extra/                エージェントのホームへコピーするファイル（今のまま）
└── sumi/secrets.txt      sumi で伏せる値の一覧（既定の場所。~/.claude/sumi から移す）
```

テンプレートに `.gitignore` を加える。クイックスタートのコピー手順は、ドットファイルも写るよう `cp -r .devcontainer/claude/template/. .devcontainer/` に変える。

### claude.toml の項目

すべて省略できる。省略した項目は既定値になる。

```toml
[agent]
uid = 1000                # 既定 1000。ホストでリポジトリを所有するユーザーに合わせる
gid = 1000                # 既定 1000

[docker]
enabled = true            # 既定 false。DinD を起動する
images = ["postgres:16", "testcontainers/ryuk:0.14.0"]   # 既定 []。DinD に読み込むイメージ

[nix]
enabled = true            # 既定 false
ref = "HEAD"              # 既定 "HEAD"。評価するコミットや ref
shell = "devShells.x86_64-linux.foo"   # 既定 ""（default の dev shell）

[sumi]
enabled = true            # 既定 false
secrets = "~/.config/claude-devcontainer/sumi/secrets.txt"   # 既定はこの値

[proxy]
ca = "/etc/ssl/certs/company-root.pem"   # 既定 ""（公開のルート証明書だけ）
connectors = ["mcpsrv_01AbCdEf"]         # 既定 []。使う claude.ai のコネクタの ID

[proxy.hosts."api.github.com"]
routes = ["POST /graphql", "GET /user", "/repos/[^/]+/[^/]+(/.*)?"]
headers = ["X-GitHub-Api-Version"]
inject = { Authorization = { basic = "x-access-token:${GITHUB_TOKEN}" } }
env = ["GH_TOKEN"]        # 既定 []。agent に GH_TOKEN=managed-by-proxy を入れる

[subnets]
proxy = "10.99.0"         # 既定は空いているものを自動で選ぶ
dind = "10.99.1"
```

- `proxy.hosts` の各ホストの書き方（`routes`、`headers`、`inject`、`${NAME}`、`basic`）と検査の内容は、今の `proxy.yaml` の `hosts` と同じにする。形式が YAML から TOML に変わるだけである。
- `proxy.hosts` の各ホストの `env` は、agent に `managed-by-proxy` というダミーの値で入れる環境変数の名前である。ツールは環境変数にトークンがないと要求を送らないので、proxy が `inject` で本物に置き換える前提でダミーを渡す。秘密の名前（`GITHUB_TOKEN`）とツールが読む名前（`GH_TOKEN`）は一致しないことがあるため、自動では決めず利用者が書く。今 `compose.yaml` に書いている `GH_TOKEN: managed-by-proxy` はこれに置き換わる。
- `env` に compose が agent に設定する名前（`HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` とその小文字、`DOCKER_HOST`、`TESTCONTAINERS_*`、`CLAUDE_CODE_OAUTH_TOKEN`、`CLAUDE_DEVCONTAINER_*`）を書くとエラーにする。複数のホストが同じ名前を挙げてもよい（値は同じダミーである）。
- パスは `~` を展開する。相対パスは `.devcontainer/` を基準にする（今は `.devcontainer/claude/` 基準で分かりにくい）。このリポジトリの sumi は `secrets = "../.secrets/sumi/secrets.txt"` になる。
- sumi の一覧の場所は1項目で書く。今の `SUMI_DIR` と `SUMI_FILE` の2項目は compose の都合なので、生成時に分ける。
- 知らない項目、型の違う値はエラーにする（綴りの誤りで設定が黙って無視されるのを防ぐ）。エラーはファイル名と項目のパス（`claude.local.toml: proxy.hosts."api.github.com".routes` など）を示す。
- ホスト名は `[proxy.hosts."api.github.com"]` のように引用符で囲む。囲み忘れると `api` → `github` → `com` の入れ子のテーブルになり、知らない項目のエラーになる。`proxy.hosts` の下でこのエラーが出たときは、メッセージで引用符を案内する。
- tomllib が読む TOML 1.0 では、インラインテーブルは1行に収める必要がある。`inject` に複数のヘッダーを書く例として、テンプレートに `[proxy.hosts."…".inject]` のサブテーブルの書き方を載せる。
- `subnets` の値は IPv4 の第3オクテットまで（`10.99.0`）。各オクテットが 0〜255 であること、`proxy` と `dind` が同じでないことを検査する。片方だけ指定した場合の扱いは今の `pick-networks.py` と同じにする。

### claude.local.toml の上書き

- テーブルは項目ごとに再帰的にマージする。
- 値は配列も含めて置き換える（`docker.images` を local に書くと、共有の一覧に足されるのではなく置き換わる）。
- `proxy.hosts` の各ホストは丸ごと置き換える。`routes`、`headers`、`inject`、`env` は混ぜない（今の `proxy.local.yaml` と同じ）。

### 反映のしかた

どの項目も、変えたら環境を停止して `devcontainer up` で起動し直せば反映される。UID、DinD・nix・sumi の有効化は、今と同じく `devcontainer up --remove-existing-container` でコンテナを作り直す。

`scripts/compose.sh` は `initializeCommand` を通らないので、compose に渡す前に `settings.py generate` を実行する。生成に失敗した場合は、原則としてエラーで止める。前回の生成物のまま続けると、生成を止めた原因である環境変数や `.devcontainer/.env` の値が compose の展開で生成物より優先され、上書きの禁止が無効になるためである。

設定の誤りで止めた環境を片付けられなくならないよう、`down`、`stop`、`kill`、`ps`、`logs` に限り、警告を出して前回の生成物のまま続ける。その場合も、禁止した名前を `env -u` で環境から外し、`--env-file /dev/null` を渡して `.devcontainer/.env` を読ませない（`--env-file` を指定すると compose は既定の `.env` を読まない）。

### 環境変数による上書きはなくす

今は起動時の環境変数が最も優先されるが、設定の入口を1つにするため受け付けない。`settings.env` の変数と同じ名前が環境変数にあると、compose だけがそれを使い、`initializeCommand` と食い違う。そのため、`settings.py` は該当する環境変数を見つけたらエラーにする。compose が自動で読む `.devcontainer/.env` に該当する項目がある場合も同様にエラーにする。

対象は前方一致の `CLAUDE_DEVCONTAINER_*` ではなく、compose が展開する名前の一覧（`CLAUDE_DEVCONTAINER_UID`、`_GID`、`_PROXY_NET`、`_DIND_NET`、`_DIND`、`_NIX`、`_SUMI`、`_SUMI_DIR`、`_SUMI_FILE`、`_PROXY_CA`、`_CONNECTORS`）と、`initialize.sh` だけが読んでいた `_NIX_REF`、`_NIX_SHELL` に限る。`_PROXY_ADDRESS` など compose がコンテナに設定するだけの名前は対象にしない。

agent の中には compose が `CLAUDE_DEVCONTAINER_DIND`、`_NIX`、`_SUMI` を設定しているため、agent の中から `devcontainer up` を実行するとこのエラーになる（このリポジトリの統合テストがそうである）。エラーメッセージには該当する名前と、`env -u` で外して実行する方法を示す。統合テストは子プロセスに渡す環境からこれらを除き、設定は `claude.local.toml` で渡す。

## 実装

### 設定を読むモジュール scripts/settings.py

ホストで動き、標準ライブラリの `tomllib` で2つのファイルを読んでマージし、検査して、生成物を書く。ホストの要件を Python 3.11 以上に上げ、古い Python では理由を表示して止める。

- `load(root)` は検査済みの設定を返す。`copy-host-config.py` や `load-images.py` などほかのスクリプトはこれを import して使い、独自に env ファイルを読む `setting()` 関数を持たない。
- `python3 settings.py generate` は `.devcontainer/.generated/` に次を書く。`initialize.sh` が最初に実行する。
  - `settings.env`: compose が読む変数。名前は今の `CLAUDE_DEVCONTAINER_UID`、`..._DIND`、`..._SUMI_DIR` などを内部の名前としてそのまま使い、`claude/compose.yaml` の変更を小さくする。パスは絶対パスにして書く（compose の相対パスの基準の問題をなくす）。値は単一引用符で囲み、compose に `$` を展開させない。改行と `'` を含む値はエラーにする。サブネットを指定していない場合は書かず、`pick-networks.py` の `networks.env` に任せる。
  - `proxy.json`: マージした `proxy.hosts`。proxy の image のビルドが読む。
  - `images.txt`: `docker.images` を1行1件で並べたもの。agent の `wait-ready.sh` が読む。`load-images.py` は同じ内容を `settings.load()` から作る（`up` の後に `docker.images` を変えて手で実行した場合も、新しい一覧を読むため）。内容が同じなので、両者が一覧から作るマーカーの名前は一致する。
  - `agent.env`: `proxy.hosts` の `env` に挙げた名前を `NAME=managed-by-proxy` で並べたもの。`claude/compose.yaml` の agent がサービスの `env_file` として読む。compose が展開に使う変数ではなく、コンテナに渡す環境変数である。
- `python3 settings.py proxy-json FILE` は、1つの `claude.toml` の `proxy.hosts` を `proxy.json` の形で出力する。テスト用の proxy の image をテンプレートからビルドするときに使う。
- `proxy.hosts` の検査は、ホストでも proxy のビルドでも同じコードで行う。`proxy/workspace_config.py` から、標準ライブラリだけで書かれた解析と検査の部分を `proxy/policy.py` に分け、`settings.py` はこれをパスで import する。`up` の時点で、どのファイルのどの項目が悪いかを示してエラーにできる。

生成物は `.devcontainer/` の下にあるので、agent からは読み取り専用である。

### 変更するファイル

| ファイル | 変更 |
| --- | --- |
| `scripts/initialize.sh` | 最初に `settings.py generate` を実行する。`setting()` 関数は `settings.env` を読むよう簡単にする |
| `scripts/copy-host-config.py` | `setting()` をやめて `settings.load()` を使う。除外する sumi の一覧の既定の場所を新しい場所に変える |
| `scripts/load-images.py` | 一覧を `settings.load()` の `docker.images` から作る（`.generated/images.txt` と同じ内容） |
| `scripts/pick-networks.py` | 変更しない（引数で渡す値の出どころが `settings.py` に変わるだけ） |
| `agent/wait-ready.sh` | 一覧を `/workspace/.devcontainer/.generated/images.txt` から読む。警告文の `.devcontainer/images.txt` を `claude.toml` の `docker.images` に変える |
| `compose.yaml` | 先頭のコメントを claude.toml の説明への参照に置き換える。sumi の既定のディレクトリを `~/.config/claude-devcontainer/sumi` に変える。agent に `env_file: [{path: ../.generated/agent.env, required: false}]` を加える（Compose 2.24 以上） |
| `proxy/Dockerfile` | `proxy.yaml` と `proxy.local.yaml` の代わりに `.generated/proxy.json` を取り込む。`/opt/proxy/` への `COPY` に `policy.py` を加える（`workspace_config.py` が import する）。ない場合（`docker build proxy` で直接ビルドした場合など）はビルドを失敗させる。`initializeCommand` と `compose.sh` が必ず生成するので、ない状態は想定しない |
| `proxy/workspace_config.py` | 入力を JSON にし、マージを除く（マージは `settings.py` が行う）。検査は `policy.py` を使う |
| `proxy/policy.py` | 新規。`proxy.hosts` の解析と検査 |
| `proxy/.dockerignore` | `!policy.py` を加える。許可したファイル以外を除外しているので、加えないとビルドの `install` が import に失敗する |
| `proxy/workspace_config.py`（CA） | CA のエラーメッセージの `CLAUDE_DEVCONTAINER_PROXY_CA` を `proxy.ca` に変える |
| `proxy/addon.py` | コメントの `proxy.yaml` を `claude.toml` の `proxy.hosts` に、コネクタのエラーメッセージの `CLAUDE_DEVCONTAINER_CONNECTORS` を `proxy.connectors` に変える |
| `proxy/credentials.py`、`proxy/README.md` | `proxy.yaml` への言及を `proxy.hosts` に変える |
| `scripts/inject-credentials.py` | コメントとログの `proxy.yaml` を `proxy.hosts` に変える |
| `agent/Dockerfile`、`dind/Dockerfile` | UID の検査のエラーメッセージの `CLAUDE_DEVCONTAINER_UID` を `agent.uid` に変える。検査そのものは `settings.py` でも行い、ビルドの検査は念のために残す |
| `scripts/settings.py` | 新規 |
| `template/claude.toml` | 新規。今の `template/claude.env`、`template/proxy.yaml`、`template/images.txt` の内容とコメントを移す |
| `template/.gitignore` | 新規。`claude.local.toml` だけを除外する。`.generated/` は `settings.py` が中に `.gitignore` を書いて自身を除外する（`pick-networks.py` と `.nix-env` と同じ方式） |
| `template/compose.yaml` | `env_file` を `[.generated/networks.env, .generated/settings.env]` にする。`GH_TOKEN: managed-by-proxy` を消し、`claude.toml` の `env` に移す |
| `template/claude.env`、`template/proxy.yaml`、`template/images.txt` | 削除 |
| `agent/skills/claude-devcontainer/SKILL.md` | 人間に頼む変更の書き先を claude.toml の項目に変える |
| `README.md`（`claude/` 内） | 編集する場所の一覧を新しいファイルに合わせる |
| `scripts/compose.sh` | compose の前に `settings.py generate` を実行する。失敗時は `down` などに限り、禁止した設定元を外して続ける（「反映のしかた」） |

このリポジトリ自身の `.devcontainer/` も、`claude.env`、`proxy.yaml`、`images.txt` を `claude.toml` 1つに置き換え、`compose.yaml` の `GH_TOKEN` を `env` に移す。

### ドキュメント

- `README.md` の「カスタマイズする」は、`claude.toml` の項目の表と、Dev Container 標準の3ファイルの説明、ホストの設定のコピー、各機能の注意点に縮める。ファイルの使い分けの説明（`claude.env` と `.env` の節）はなくなる。利用者が目にするエラーメッセージは、内部の変数名ではなく `claude.toml` の項目名で書く。
- 「ホストに必要なもの」の Python を 3.11 以上にする。
- 「クイックスタート」を次のように直す。
  - 1.のコピー手順を `cp -r .devcontainer/claude/template/. .devcontainer/` にする（`.gitignore` も写るように）。
  - 1.の構成図から `claude.env`、`proxy.yaml`、`images.txt` を消し、`claude.toml`（機能・接続先・イメージの設定）、`.gitignore`、任意の `claude.local.toml` を載せる。
  - 3.の「`proxy.yaml` が参照する秘密」を「`claude.toml` の `proxy.hosts` の `inject` が参照する秘密」にする。
- `docs/network-policy.md` の接続先の例を TOML に書き換える。「ホストだけの接続先を追加する」は `claude.local.toml` の説明にする。
- `docs/usage.md` の DinD の節のイメージの一覧を `docker.images` にする。
- `docs/testing.md` の設定値の書き先を `claude.toml` にする。subnet と UID を環境変数で指定する説明は `claude.local.toml` での指定に変え、agent の中で実行するときに設定の環境変数を外すことを書く。
- `docs/troubleshooting.md` の「`proxy.yaml` が参照する秘密」を「`proxy.hosts` の `inject` が参照する秘密」にする。

## テスト

- `settings.py` の単体テスト（新規）: 既定値、マージ（テーブルの再帰的なマージ、配列の置き換え、ホスト単位の置き換え）、知らない項目と型の誤りのエラー、パスの展開と基準、`settings.env` の書式（単一引用符、`$` を含む値、改行と `'` の拒否）、環境変数と `.devcontainer/.env` に設定の名前がある場合のエラー（`_PROXY_ADDRESS` などはエラーにしないこと）、ホスト名の引用符の誤りの案内、`subnets` の検査、`.generated/.gitignore` の生成、`agent.env` の内容と `env` に予約された名前を書いた場合のエラー、Python のバージョン確認。
- `proxy/workspace_config_test.py`: 入力を JSON に変える。検査のケースは今のものを `policy.py` に対して流用する。
- `tests/settings_integration_test.py`: 今の「環境変数・.env・claude.env の優先順位」のテストを、「claude.toml と claude.local.toml から生成した値を compose と initialize が同じように使う」テストに置き換える。sumi の相対パスのテストは `.devcontainer/` 基準で書き直す。
- `tests/devcontainer_integration_test.py`: `env` に挙げた `GH_TOKEN` が agent で `managed-by-proxy` になっていることを確かめる。子プロセスの環境から設定の名前を除き、`CLAUDE_DEVCONTAINER_DIND=1` や sumi の設定は `claude.local.toml` に書く。
- `compose.sh` のテスト（新規）: 生成に失敗したとき、`up` や `build` は止まり、`down` は続くこと。続けた場合に、環境変数と `.devcontainer/.env` の値が compose の展開に使われないこと（`docker compose config` で確かめる）。
- `tests/copy_host_config_test.py`、`tests/load_images_test.py`、`tests/devcontainer_integration_test.py`: 設定の書き方を TOML に変える。

## 決めたこと

- 形式は TOML。ホストの Python の標準ライブラリで読めるため（YAML は PyYAML が要る）。代わりにホストの要件が Python 3.11 以上になる。
- 古いファイルとの互換は持たない。利用者がまだいないため。
- ダミーのトークンは `proxy.hosts` の各ホストに `env` で書く。接続先・注入する認証・ダミーを1か所にまとめるため。名前は秘密の名前から推測できないので、利用者が明示する。
- 生成物の変数名は今の `CLAUDE_DEVCONTAINER_*` を内部の名前として残す。compose、agent のスクリプト、dind-bridge の変更を小さくするため。
- compose が環境変数しか読めないことと、proxy のルールを image に組み込むことは変えない。変換を `initializeCommand` に寄せ、利用者からは見えなくする。

## 対象外

- `credentials` と `extra/` の形式。ホストのシェルで実行する、作業ツリーの外に置くという今の性質を保つ。
