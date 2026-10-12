# ワークスペースの共有と分離を選べるようにする

## 目的と位置づけ

現在の独立した clone に加え、ホストの作業ツリーをそのまま使うモードを選べるようにする。未コミットの変更を含めて同じファイルを編集したい場合は共有を、ホストの作業ツリーと Git の状態を保護したい場合は分離を使う。

この文書は実装前の設計案である。設定、起動、切り替え、保存するデータ、信頼の境界を定める。実装手順と個々のテストの追加順序は、設計を確認した後の実装計画で扱う。

[既存の分離設計](2026-10-12-isolated-clone-design.md)を拡張し、既定の動作は維持する。

## 設定と利用者から見た動作

`.devcontainer/claude.toml` または `.devcontainer/claude.local.toml` に指定する。

```toml
[workspace]
mode = "clone" # 既定値。共有する場合は "shared"
```

設定の優先順位は既存と同じく、既定値、`claude.toml`、`claude.local.toml` の順とする。省略時は `clone`。値は `clone` と `shared` のみ許可し、タイプ違いと未知の値は起動前にエラーにする。環境変数からの上書きは追加しない。

| 項目 | `clone`：分離 | `shared`：共有 |
| --- | --- | --- |
| `/workspace` | 既存の専用 Docker volume | ホストのリポジトリルートを read-write bind mount |
| 作業開始時の内容 | 初回のみホストのローカル HEAD と履歴をコピー | ホストの現在のファイル。未コミット・未追跡も見える |
| Git の状態 | 独立した `.git` | ホストと同じ `.git` |
| ブランチ | 初回作成時だけホストに合わせ、以後は独立 | ホストと常に同じ。どちらかの切り替えが他方にも反映される |
| 変更の受け渡し | ホストの `agent` リモートで fetch / push | 編集とコミットがそのままホストに反映される |
| コンテナ再作成 | 保存済み clone を再利用 | その時点のホストの作業ツリーを再びマウント |
| Node / Gradle の依存 | 既存の CoW マウント | 同じ CoW マウント |

設定変更はコンテナの再作成で反映する。起動コマンドは共通のままとする。

```sh
devcontainer up --workspace-folder . --remove-existing-container
```

最初の実装では `sbx run --clone` に相当する専用 CLI は追加しない。VS Code と devcontainer CLI が同じ設定を使い、起動方法によってモードが変わらない形を優先する。

## 構成

```text
clone                              shared
ホストのリポジトリ                  ホストのリポジトリ
  └─ 初回のローカル Git コピー        └─ read-write bind mount
        ↓                                    ↓
  /workspace（専用 volume）           /workspace（同じ作業ツリー・.git）
        ↑
  ホストから git fetch/push agent

共通：Node / Gradle の CoW、proxy、firewall、任意の DinD / Nix / sumi
```

モードで変える範囲は `/workspace` のマウント、初期 clone、所有権の準備、`agent` リモートの利用可否に限定する。agent サービスを二重に定義せず、ビルド、ネットワーク、認証、依存マウントは共通に保つ。同じホストの作業ツリーについて、二つのモードを同時には起動しない。

### 設定生成と Compose

`settings.py` がモードを検証し、既存の `settings.env` に加えて `.generated/workspace.compose.yaml` を生成する。この小さな Compose ファイルは `agent` の `/workspace` マウントだけを定義する。

- `clone` は現在の `workspace` named volume を使う。
- `shared` は解決済みのホストのリポジトリルートを bind mount する。パスを文字列連結で YAML に埋め込まず、適切にシリアライズする。存在しない source の自動作成は無効にする。

既存の Compose include を、core と生成ファイルを一組としてマージする構成にする。core の相対パスの基準と `settings.env` の適用範囲は維持する。利用者にモードごとの Compose 編集を要求しない。外側の Compose にある Gradle や追加の Node マウントも引き続き利用できる。

Compose の project 名と `workspace` volume 名は切り替えで変更しない。shared 中も既存 volume を削除せず、agent には接続しない。設定確認用の `compose config` でも同じ構成が得られることを検証する。

設定生成は Git コピーや remote 登録を行わない。`compose.sh` から設定を確認するだけで、Git の状態や保存済み workspace が変わらないようにする。

### 起動処理

`initialize.sh` は、検証済みの設定に従って次のように分岐する。

| 処理 | `clone` | `shared` |
| --- | --- | --- |
| 設定生成・共有モードの前提確認 | 実行 | 実行 |
| `workspace-source.py` による Git コピー | 実行 | 実行しない |
| ホストの `agent` remote の登録 | 実行 | 実行しない |
| CoW ディレクトリの準備 | 実行 | 実行 |
| Nix、ネットワーク、認証などの準備 | 現行どおり | 現行どおり |
| `wait-ready.sh` の初期 clone | 実行 | 実行しない |

モードは生成設定からコンテナの環境変数と label に渡し、コンテナ作成時に確定する。起動中のコンテナは、ホストの TOML や更新された生成ファイルを読み直してモードを切り替えない。残っている `workspace.json` や `/workspace/.git` の有無からモードを推測しない。

shared では初期 clone の完了だけを待たずに進む。proxy、認証、DinD の image 読み込みなど、既存の起動待ちは維持する。ログに実際のモードを出し、shared ではホストの作業ツリーと Git の状態へ直接書き込むことを一度表示する。

### 所有権とマウントポイント

現在の `workspace_mounts.py` は、volume のルートと依存マウントまでの親ディレクトリを node の所有にする。この処理は `clone` で維持する。

`shared` ではホストの作業ツリーのルート、親ディレクトリ、`.git` に対して chown / chmod しない。root による準備は、実際にマウントされた CoW の依存ディレクトリのルートに限り、symlink をたどらない。依存マウントの親はホストで事前に存在することを確認し、Docker に root 所有の親を自動作成させない。

共有する作業ツリーは、設定した `agent.uid` / `agent.gid` の node が必要な読み書きをできることを前提とする。起動時に workspace と Git 管理ディレクトリのアクセスを node で検査し、使えない場合は対象パスと UID / GID の設定箇所を示して停止する。自動的な権限変更や `safe.directory=*` による回避は行わない。

## モード切り替えとデータの保持

| 切り替え | 再作成後に使う内容 | 保持するもの |
| --- | --- | --- |
| `clone` → `clone` | 今までの clone | clone のブランチ、未コミット変更、履歴 |
| `clone` → `shared` | ホストの現在の作業ツリー | clone volume は取り外したまま保存 |
| `shared` → `clone`、保存済み clone あり | 以前の clone の続き | shared 中に進んだホストの状態もそのまま |
| `shared` → `clone`、clone が未作成 | その時のホスト HEAD と同じブランチで初期化 | ホストの未コミット変更はホストに残る |
| `shared` → `shared` | ホストの現在の作業ツリー | ホストのファイルと Git の状態 |

切り替えは保存先の選択であり、同期や作業の移行は行わない。clone の成果を shared に持っていく場合は、切り替える前に既存の `git fetch agent` とレビュー・取り込みを行う。shared から保存済み clone に変更を渡す場合は、ホストでコミットし、clone で起動した後に `git push agent` を使う。

以前の clone に戻す際にホストと同じブランチへ戻したり、reset / 自動 fetch したりしない。通常の切り替えに `down -v` や volume の削除を含めない。初期化途中の clone は既存の再試行規則に従い、shared に切り替えたことで初期化記録を消さない。

## `agent` リモート

`clone` では現行どおり自動登録し、ホストから Docker exec 経由で Git に接続する。`shared` の初回起動では登録せず、既存の同名 remote も検査・変更しない。

clone で登録した `agent` remote と `protocol.ext.allow` は、shared に切り替えても自動削除しない。remote 設定は linked worktree 間で共有されるため、一つの作業ツリーの切り替えで別の clone 環境を壊さないためである。

代わりに `git-agent.py` は、接続先の実行中コンテナを特定した後、Docker inspect で mode label と `/workspace` の実際のマウントを確認する。shared のコンテナに対する upload-pack / receive-pack は、Git プロセスを起動する前に拒否する。同じホストリポジトリに対する意図しない fetch / push を防ぐ。

判定には現在の TOML を使わない。たとえば設定を shared に編集しても、まだ動いている clone からは成果を取得できる。label とマウントが食い違う場合は停止する。更新前の label がないコンテナは、`/workspace` がその Compose project の既存 `workspace` named volume と確認できる場合に限って clone と扱い、それ以外は再作成を案内する。

## 依存と Git リポジトリの対応範囲

Node と Gradle は両モードで同じ CoW 方針を使い、既存の upper / work と volume の識別子を維持する。モード別に依存キャッシュを増やさず、切り替えで消したり作り直したりしない。shared でも、CoW マウントされた `node_modules` 内への書き込みはホストの lower に反映されない。一方、`package.json` や lockfile は共有される。

CoW はスナップショットではない。shared でも、コンテナ使用中にホストで lower の依存を更新しない。ホスト側の依存更新はコンテナを削除してアンマウントした後に行い、必要に応じて既存の手順で Node の upper / work を明示的に再作成する。追加パッケージの `node_modules` は既存どおり個別のマウント設定が必要で、共有モードにしただけでは CoW にならない。

初期対応範囲を次のようにする。

- `clone` は現在の通常リポジトリ、linked worktree、detached HEAD、shallow repository の対応を維持する。初期化にはコミットが必要である。
- `shared` はルートに実体の `.git` ディレクトリがある通常リポジトリを対象とする。Git 管理ディレクトリがリポジトリ内に収まることを起動前に確認する。初回コミット前でも共有自体は利用できるが、Nix のコミット指定など各機能の前提は変わらない。
- shared の linked worktree、外部 git-dir / common-dir / object alternates は初期対応に含めず、検出時には clone を使う案内とともに停止する。ホストの外部パスを参照する `.git` ファイルをそのままマウントして、コンテナ内だけ Git が壊れる状態にはしない。

linked worktree の shared 対応には、worktree 専用管理ディレクトリと共通 `.git` のマウント、およびホストとコンテナで異なるパスの処理が必要になる。今回はそのためにホストの親ディレクトリを広く共有したり、ホストの Git 設定を書き換えたりしない。

## 共有モードの信頼の境界

shared は、エージェントによるホストのリポジトリの直接変更を利用者が許容するモードである。作業ファイルだけでなく、index、refs、Git 設定、hooks、`.devcontainer/` も変更できる。ホスト側の `git status` / `git diff` を、エージェントから独立したレビューの根拠にはできない。ホストでビルドや次回起動を行うと、変更されたファイルや設定が実行され得る。

proxy、firewall、認証情報の代理注入、ホスト Docker socket を渡さない構成は維持する。ただし、これらがホストの作業ツリーや Git を保護するわけではない。ホストの `.env` や `.secrets/` など、リポジトリ内の未追跡・ignored ファイルも共有される。sumi を使っていても、それらのファイルが自動で隠れるわけではない。

`/run/host/generated` と `/run/host/nix-env` の read-only マウントは、共通の参照先として維持する。ただし shared では同じ内容に `/workspace/.devcontainer/` から書き込めるため、管理用生成物が改ざんできないとは扱わない。共有モードのために `.git` や `.devcontainer` の一部だけを隠す仕組みは追加しない。

clone に戻しても、shared 中に変更されたホストのコードや設定の信頼性が回復するわけではない。必要な確認はホスト側で行う。

## 検証すること

実装時には既存の分離テストを維持し、次を追加する。

1. 設定省略で clone、ローカル設定で shared、不正な値と環境変数の上書きはエラーになる。
2. 両モードの Compose 解決結果で `/workspace` のマウントが一つだけになり、共通サービスと外側の追加マウントが保たれる。
3. shared の初回起動で未コミット・未追跡ファイルが見え、編集、commit、ブランチ切り替えが双方向に反映される。Git コピーと remote 登録は起きない。
4. shared の起動でホストの workspace と親ディレクトリの UID / GID / mode が変わらず、権限不足や未対応 Git 配置では明確に停止する。
5. clone → shared → clone と切り替えて、clone の未コミット変更とブランチが保存され、共有側の変更が勝手に混ざらない。clone 未作成の場合はその時点のホストから初期化される。
6. shared に接続する `git fetch agent` / `git push agent` を拒否する。設定ファイルだけ変更した場合、コンテナ再作成後、旧コンテナの場合も実際の接続先に基づいて判定する。
7. 両モードで依存マウントへの編集がホストの lower を変えず、切り替えで upper が失われない。Gradle と Testcontainers の既存シナリオも成立する。
8. shared でも proxy と firewall の既存制限が保たれ、clone のホスト隔離テストも引き続き通る。

shared の実コンテナ試験には専用の一時リポジトリを使い、開発中のこのリポジトリをテスト対象として書き換えない。

## 比較した方式と対象外

| 方式 | 判断 |
| --- | --- |
| 設定で選び、workspace マウントと初期化だけを分岐 | 採用。既存の起動方法と共通機能を維持できる |
| shared 用の agent サービスや devcontainer 定義を別に持つ | 採用しない。共通設定の重複と volume 名の変化を増やす |
| CLI フラグだけで選ぶ | 初回実装では採用しない。VS Code からの起動にも別の設定経路が必要になる |

作業ツリーの自動同期、未コミット変更のコピー、切り替え時の merge、二つのモードの同時実行、linked worktree の shared 対応、依存管理方式の刷新、専用 CLI は今回の対象外とする。
