# エージェントの作業場所をホストのリポジトリから切り離す

## 目的

今の構成では、ホストのリポジトリ（作業ツリーと `.git`）をエージェントに書き込み可能でマウントしている。当初は、ホストでコードが実行される経路に応じて、次の2つの方針で安全性を担保するつもりだった。

- `~/.bashrc`、`node_modules`、`.git/hooks` のように、気付かないところで実行される経路は、read-only でマウントするか、そもそもマウントしないことで改ざんを防ぐ。
- `package.json` や `.envrc` はエージェントが編集できるようにし、`pnpm run` や `direnv allow` の前に人間が差分をレビューする。

しかし、後者はレビューに使う差分を信頼できることが前提だった。エージェントはホストの index も書き換えられるため、`git update-index --assume-unchanged` などで変更を差分から隠せる。人間が実行前に `git status` や `git diff` を確認しても、レビューすべき変更が表示されなければ、差分によるレビューは成立しない。

Git の状態を書き換えられると、read-only にしたファイルにも影響が及ぶ。たとえば、`git hash-object -w` と `git update-index --cacheinfo` で悪意ある `.devcontainer/devcontainer.json` を index に登録し、`--assume-unchanged` を付けて commit する。すると、作業ツリーは安全なまま HEAD だけが改ざんされ、`git status` も `git diff HEAD -- .devcontainer` も差分を報告しない。ホストで `git reset --hard HEAD` を実行すると改ざんされた内容が作業ツリーに書き出され、次の `devcontainer up` でホストがそれを実行する（git 2.54.0 で再現を確認した）。`initialize.sh` が nix の dev shell をコミットから評価するのも、エージェントが ref を動かせるため同じ問題を抱えている。

人間が変更を確認してから実行するという方針を成立させるため、エージェントがホストの作業ツリーやレビューに使う Git の状態を直接書き換えられない構成にする。

- エージェントは専用の clone（Docker volume）で作業する。ホストの作業ツリーも `.git` もマウントしない。
- ホストの`agent`リモートをコンテナ内リポジトリへ直接接続し、通常の`git fetch agent` / `git push agent`で受け渡す。GitHubなどの外部リポジトリや専用の利用者向け転送コマンドは使わない。
- 人間はホストでコミット間の差分をレビューし、承認したコミットIDを指定して取り込む。承認した変更は信頼済みとして扱い、ホストでテストしたり、IntelliJ や Eclipse で開いたりできる。

Git の push / fetch では、エージェント側の index や `assume-unchanged` の状態は転送されない。ホスト側で信頼済みのコミットと受け取ったコミットを比較するので、エージェントが自分の index を操作しても、レビューする差分を隠せない。

## 前提

初回起動はホストのローカルHEADとその履歴をコピーする。外部リモートや事前のpushは不要で、未pushのコミットも使える。未コミットの変更と未追跡ファイルはcloneに入らない。

起動時にホストの`agent`リモートを自動登録する。Git標準の`ext`転送を使い、Docker execの標準入出力をコンテナ内の`git-upload-pack` / `git-receive-pack`につなぐ。コンテナは接続ごとにworkspaceとserviceのlabelで特定し、必ずnodeでGitを実行する。SSHサーバーや公開ポートは作らない。既存の`origin`は保持し、別用途の`agent`リモートが存在すれば上書きせず停止する。`protocol.ext.allow=always`はホストの当該リポジトリにのみ設定する。

エージェントは作業用ブランチ（例：`agent/task-name`）にコミットし、人間がホストで取得・レビューする。コンテナからホストへ接続するリモートやホストDocker socketは渡さない。

## シナリオ

| # | シナリオ | 満たし方 |
| --- | --- | --- |
| 1 | Java の `./gradlew --offline jar test` | 既存の `~/.gradle` の CoW マウントを維持し、ホストで取得済みの配布物と依存を使う。Testcontainers は DinD で動かす |
| 2 | Node のビルド・テスト | ホストで用意した依存を利用する。`./node_modules` の CoW マウントを使う |
| 3 | コンテナでは動かないテストをホストで流す | エージェントがコミットし、チャットで依頼する。人間がレビュー後、そのコミットを別 worktree に取り出してテストし、結果をチャットで返す |
| 4 | コンテナではできない作業をホストに頼む | エージェントがスクリプトをコミットに含める。人間がレビュー後に実行し、結果をチャットで返す |
| 5 | エージェントの成果を取り込む | ホストで `git fetch` → コミットIDを固定 → レビュー → そのIDを merge |
| 6 | ホストの変更をエージェントに渡す | ホストで`git push agent HEAD:refs/heads/host/main`、エージェントがローカルの`host/main`をmerge / rebase |
| 7 | 作業中の様子を見る | VS Code でコンテナの clone を見る |
| 8 | コンテナを作り直しても作業が残る | clone と依存のキャッシュは volume に残す。`claude --resume` も今のまま使える |

## 使い方

`agent`はホストからコンテナへ直接接続するリモート名である。

### 初回起動

VS Code の「Reopen in Container」か `devcontainer up` で起動する。ホストのローカルHEADとその履歴をコピーし、空のworkspace volumeにホストと同じブランチ名でcheckoutする。ホストがdetached HEADならコンテナ側もdetached HEADにする。ブランチ名を合わせるのは初回作成時だけで、以降はコンテナ側のブランチを維持する。事前のpushやコンテナ内での手動cloneは不要である。未pushのコミットも使えるが、未コミット・未追跡ファイルはコピーしない。

### 受け渡し用リモート

起動処理がホストに`agent`リモートを自動登録する。接続先は起動中のコンテナ内の`/workspace`であり、GitHubなどの外部リポジトリは介在しない。既存の`origin`は変更しない。同名の`agent`が別用途で存在する場合は上書きせず停止するので、既存のリモートを別名にしてから起動する。

Git標準の`ext`転送とホスト側の`git-agent.py`を使い、Dockerの標準入出力をコンテナ内の`git-upload-pack` / `git-receive-pack`につなぐ。どちらも`node`で実行する。SSHサーバーや公開ポートは不要で、接続時にコンテナを見つけ直すため再作成にも追従する。このホストリポジトリだけに`protocol.ext.allow=always`を設定する。接続にはコンテナが起動している必要がある。

エージェントは引き継いだブランチで作業できる。必要なら次のように別の作業ブランチを作る。コミット後はブランチ名とコミットIDを人間に伝える。pushは不要。

```sh
git switch -c agent/task-name
# 編集、テスト、commit
```

コンテナにはホストへ書き戻すためのリモートやホストのDocker socketを渡さない。受け渡しの操作はホストから行う。

### ホストでレビューして取り込む

```sh
git fetch --no-tags agent refs/heads/agent/task-name
agent_commit=$(git rev-parse --verify 'FETCH_HEAD^{commit}')
base_commit=$(git rev-parse --verify 'HEAD^{commit}')
git log --oneline "$base_commit..$agent_commit"
git diff "$base_commit" "$agent_commit"
```

レビューでは、ホストで実行する `package.json`、`.envrc`、ビルドスクリプト、`.devcontainer/` などの変更も確認する。`--no-tags` とブランチを指定した fetch により、レビューと無関係なタグを取り込まない。

承認したら、同じコミットIDを取り込む。

```sh
test "$(git rev-parse HEAD)" = "$base_commit" && git merge --ff-only "$agent_commit"
```

レビュー中にエージェントが追加でcommitしたり、IDE が background fetch したりしても、変数に保存したIDは変わらない。ホストの HEAD が変わった場合は差分を確認し直す。ff できなければホストの変更を push し、エージェントに rebase を依頼して、更新後のコミットを再びレビューする。

ホストでテストするために別 worktree へ取り出す場合も、レビュー済みのIDを指定する。

```sh
git worktree add --detach ../repo.agent "$agent_commit"
```

取り出したファイルは IDE やビルドツールから実行されるため、別 worktree への取り出しもレビューのあとに行う。`git pull` は fetch と取り込みを続けて行うので、このレビュー手順では使わない。

### ホストの変更を渡す

ホストから、コンテナの受け取り用ブランチへpushする。

```sh
git push agent HEAD:refs/heads/host/main
```

エージェントはコンテナ内で`git merge host/main`または`git rebase host/main`を実行する。コンテナでcheckout中のブランチへのpushはGitが拒否するので、`host/main`など別のブランチへ送る。コンテナの未コミットの作業を自動で上書きしない。


## 構成

```text
ホストのリポジトリ                        agent コンテナ
  作業ツリーと .git                         /workspace  専用 volume の clone
       └──── Git ext + Docker exec（node）──────▶ │
       （ホストから fetch / push、外部Gitサーバー不要）

  .devcontainer/.generated/ ── ro ──▶ /run/host/generated
  .devcontainer/.nix-env/   ── ro ──▶ /run/host/nix-env
  ~/.gradle ── CoW ───────────────▶ ~/.gradle（既存の構成を維持）
  ./node_modules ── CoW─▶ /workspace/node_modules
```

Compose の `workspace` volume はプロジェクトごとに作る。依存の共有には、ホスト側へ書き戻さない CoW マウントを使う。Gradle の既存構成を維持し、Node はホストの `node_modules` を lower 層とする。clone 内の `.devcontainer/` や `.git/config` はエージェントが編集できる。

ホストでの起動、ビルド、proxy 設定の生成には、ホストの `.devcontainer/` を使う。clone 内の設定変更は、人間がホストに取り込んで起動し直すまで反映されない。

### ホストの生成物

`initialize.sh` は既存の設定生成に加え、初期 clone 用の `.devcontainer/.generated/workspace.json` を生成する。内容は、ホストのHEADの完全なコミットID（`commit`）、ブランチ名（`branch`、detached HEADなら空文字）、初期化用bare repositoryの名前（`seed`）である。ホスト側でローカルGit転送を使い、指定コミットと履歴を`.generated/workspace-<commit>.git`へ独立してコピーする。ホストの`.git/config`、hooks、index、alternatesやハードリンクは引き継がない。linked worktreeとdetached HEADにも対応する。コピーの完了後にmetadataをatomicに公開する。同一コミットのコピーは再利用し、過去のコピーは中断した初期化の再試行のため保持する。

metadataにはリモートURLを含めない。コミットのないリポジトリは停止する。

`.generated/` と `.nix-env/` はホストで作成し、専用のパスに read-only でマウントする。`wait-ready.sh` は `/run/host/generated/images.txt` を、`bash-env.sh` は `/run/host/nix-env/env.rc` を読む。コンテナ内の管理スクリプトは、エージェントが編集できる clone 内の `.devcontainer/` から設定を読まない。

### 自動 clone と再起動

Git を実行する初期化処理は `postStartCommand` の `wait-ready.sh` から node ユーザーで呼ぶ。entrypoint は従来どおり CA と firewall を設定して権限を落とす。`waitFor: postStartCommand` により、自動 clone が完了するまで起動完了として扱わない。

1. 正常な clone が既にあれば、そのまま使う。自動 fetch、checkout、reset はしない。ホストの HEAD が進んでも、未コミットの変更やエージェントのブランチを維持する。
2. Git リポジトリがなく、依存のマウントポイント以外にファイルがない workspace では Git リポジトリを初期化し、コンテナ側のリモートは登録しない。初期化途中であることと、開始時のコミットID / ブランチ名 / コピー名を `.git/claude-bootstrap.json` に記録する。
3. `/run/host/generated/`内の指定bare repositoryからGitのローカル転送でオブジェクトを取り込む。共有ハードリンクやalternatesは作らない。リモートへの接続や認証は不要である。コピーが読めない場合は停止し、ホストでコピーを確認してから再試行する。
4. 指定コミットが取得できたことを確認し、記録したブランチ名を作ってcheckoutする。ブランチ名が空ならdetached HEADにする。中断後の再試行でブランチが既にあれば、同じコミットであることを確認して再利用し、進んでいる場合は上書きせず停止する。成功後に初期化途中の印を消す。

途中で失敗した場合は、記録したコミットID / ブランチ名 / コピー名で初期化を再試行できる。既存のファイルを削除したり、強制 checkout したりはしない。初期化途中の印がない不完全なリポジトリや、Git リポジトリでないファイルが既にある場合は停止し、人間に確認を求める。通常の起動に、既存 volume を消す処理は含めない。

ホストからコンテナ内の Git を操作する場合も、`docker exec --user node` を使う。エージェントが設定できる hook や `core.fsmonitor` を、コンテナの root 権限で実行させないためである。

### 依存の共有

Gradle は既存の `~/.gradle` の CoW マウントを維持する。ホストで取得済みの配布物と依存を使い、エージェントの書き込みは upper 層に残す。独立した clone への移行を理由に、registry の許可や Java の proxy 設定を追加しない。

Node は、ホストの `./node_modules` を lower 層、エージェント専用のディレクトリを upper / work 層とする overlay volume を `/workspace/node_modules` にマウントする。ホストでインストール済みの依存を使い、エージェントの追加・変更・削除は upper 層にだけ残す。lower がなければホストで空のディレクトリを作る。upper / work はホストの状態保存ディレクトリにリポジトリごとに分けて置き、通常の再起動・再作成では保持する。

既定で対象にするのはルートの `node_modules` である。モノレポで各パッケージにも `node_modules` がある場合は、必要なディレクトリを同じ方式で追加マウントする。npm・通常の pnpm・Yarn の node_modules モードを対象にし、Yarn PnP や外部の virtual store を指す構成を自動対応の対象にはしない。native addon を使う場合は、ホストとコンテナの Node・libc などに互換性が必要である。CoW は既存の依存を再利用する仕組みであり、新しい依存の取得は自動化しない。

CoW はスナップショットではない。overlay の利用中にホストの lower 層を変更しないという既存の Gradle の制約を維持する。ホストでの依存更新はコンテナを停止・削除して overlay がアンマウントされたあとに行う。Node の依存を更新したら、必要なエージェント側の変更を先に退避し、Node 用の upper / work を明示的に作り直してから起動する。clone の workspace volume や Gradle の upper は削除しない。自動で upper を消す処理は通常の起動に含めない。

初回の `/workspace` に依存のマウントポイントが存在することを許容する。実際にマウントされている `node_modules` と、そこへ至るディレクトリだけを初期化時の例外にし、任意の既存ファイルは受け入れない。checkout 対象のコミットが依存マウントのパスを追跡している場合は停止し、依存の内容を checkout で上書きしない。

sumi の一覧は引き続きホストから sumi サービスにだけ共有する。ホストの `.secrets/` を隠すための workspace 上の mount は不要になるが、Git にコミットした demo の値は clone にも入る。実際の秘密を Git に入れないという前提は変わらない。

## 検証すること

- 初回起動がローカルコピーから未pushの指定コミットを自動取得し、完了後に`/workspace`を使える。
- リモートが利用不能でも初期化でき、ローカルコピーの読み込み失敗・初期化の中断から再試行できる。
- 自動登録した`agent`で、proxy停止中でもホストからfetch/pushできる。
- 再起動・再作成しても、cloneのブランチ、未コミットの変更、依存のキャッシュが残り、リモート接続が新しいコンテナへ追従する。
- エージェントが `.devcontainer/`、Git の設定、index、refs を変更しても、ホスト側は変わらない。
- エージェントの`assume-unchanged`がホストへ渡らず、直接fetchしたコミットの差分をホストで確認できる。
- レビュー後にリモートが進んでも、指定したコミットIDだけを取り込める。
- nix の環境と DinD の image 一覧を、ホストの生成物から引き続き読める。
- Gradle、npm、Testcontainers のシナリオを新しい volume 構成で実行できる。

## 対象外

- 外部Gitリポジトリを経由する受け渡し、bundle、`cdc`コマンド
- 未コミット・未追跡ファイル（`.env` など）の自動転送
- ホストへの作業依頼と結果返却の自動化
- 複数エージェント間での依存キャッシュの共有
- エージェントの dev server をホストのブラウザで見るためのポート転送
