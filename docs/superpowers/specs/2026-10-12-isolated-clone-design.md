# エージェントの作業場所をホストのリポジトリから切り離す

## 目的

今の構成では、ホストのリポジトリ（作業ツリーと `.git`）をエージェントに書き込み可能でマウントしている。当初は、ホストでコードが実行される経路に応じて、次の2つの方針で安全性を担保するつもりだった。

- `~/.bashrc`、`node_modules`、`.git/hooks` のように、気付かないところで実行される経路は、read-only でマウントするか、そもそもマウントしないことで改ざんを防ぐ。
- `package.json` や `.envrc` はエージェントが編集できるようにし、`pnpm run` や `direnv allow` の前に人間が差分をレビューする。

しかし、後者はレビューに使う差分を信頼できることが前提だった。エージェントはホストの index も書き換えられるため、`git update-index --assume-unchanged` などで変更を差分から隠せる。人間が実行前に `git status` や `git diff` を確認しても、レビューすべき変更が表示されなければ、差分によるレビューは成立しない。

Git の状態を書き換えられると、read-only にしたファイルにも影響が及ぶ。たとえば、`git hash-object -w` と `git update-index --cacheinfo` で悪意ある `.devcontainer/devcontainer.json` を index に登録し、`--assume-unchanged` を付けて commit する。すると、作業ツリーは安全なまま HEAD だけが改ざんされ、`git status` も `git diff HEAD -- .devcontainer` も差分を報告しない。ホストで `git reset --hard HEAD` を実行すると改ざんされた内容が作業ツリーに書き出され、次の `devcontainer up` でホストがそれを実行する（git 2.54.0 で再現を確認した）。`initialize.sh` が nix の dev shell をコミットから評価するのも、エージェントが ref を動かせるため同じ問題を抱えている。

人間が変更を確認してから実行するという方針を成立させるため、エージェントがホストの作業ツリーやレビューに使う Git の状態を直接書き換えられない構成にする。

- エージェントは自分専用の clone（Docker volume）で作業する。ホストの作業ツリーも `.git` もマウントしない。
- ホストとエージェントの間で受け渡すのは、commit 済みの変更を入れた Git の bundle だけにする。
- エージェントの変更がホストに入るのは、人が差分を見て承認したときだけにする。**承認した時点で、その変更を信頼したものとみなす。** 承認のあとは、ホストでテストを流しても、IntelliJ や Eclipse（Dev Container に対応していない）で開いてもよい。

bundle ではエージェント側の index の状態を受け渡さず、ホスト側で信頼済みのコミットと受け取ったコミットを比較する。これにより、エージェントが index を操作しても、ホストでレビューする差分を隠せない。

「隔離された場所で作業し、commit 単位で確認して持ち帰る」という流れだけで説明できる構成を目指す。

## シナリオ

| # | シナリオ | 満たし方 |
| --- | --- | --- |
| 1 | Java の `./gradlew jar test` | Gradle 本体と依存は proxy 経由で取得する。`~/.gradle` はコンテナの再作成後も残す。Testcontainers は DinD で動かす |
| 2 | Node の `npm ci && npm test` | `node_modules` はエージェントの volume の中に置き、ホストとは共有しない。`~/.npm` は残す |
| 3 | コンテナでは動かないテストをホストで流す | エージェントがチャットで依頼する。人が `cdc pull --worktree` で別 worktree に取り出してテストし、結果をチャットで返す |
| 4 | コンテナではできない作業をホストに頼む | エージェントがスクリプトを commit に含め、チャットで依頼する。人がコードと一緒にレビューし、取り出して実行し、結果をチャットで返す |
| 5 | エージェントの成果を取り込む | `cdc pull` |
| 6 | ホストの変更をエージェントに渡す | `cdc push` |
| 7 | 作業中の様子を見る | VS Code でコンテナに接続して見る。ホスト側の専用の仕組みは作らない |
| 8 | コンテナを作り直しても作業が残る | workspace は volume に置く。`claude --resume` も今のまま使える |

## 使い方

```
cdc push              ホストのコミットをエージェントに渡す
cdc fetch             エージェントの HEAD を refs/remotes/agent/main に取り込む（作業ツリーは変えない）
cdc merge             refs/remotes/agent/main をホストの作業ツリーに ff-merge する（fetch はしない）
cdc merge --worktree  refs/remotes/agent/main を別 worktree に取り出す
cdc pull              fetch → コミット一覧と変更行数を表示 → 承認 → merge
```

起動は今までどおり VS Code の「Reopen in Container」か `devcontainer up` で行う。初回の起動で volume が初期化される（後述）。

中身を読んでから取り込むときは、`cdc fetch` のあと `git log -p agent/main`、tig、IDE の diff 画面などでレビューし、`cdc merge` で取り込む。`refs/remotes/agent/main` が動くのは `cdc fetch` のときだけなので、レビューしたコミットと merge するコミットはずれない。`cdc pull` は、変更行数を見るだけで取り込んでよいときの近道である。表示したコミットと merge するコミットは同じで、表示のあとにエージェントが進めたコミットは入らない。

どの経路で取り込んだ場合も、取り込んだ時点で信頼したものとみなす。

エージェントは、渡したい変更を commit しておく。途中の状態でも WIP commit でよい。未コミットの変更、未追跡ファイル、index の状態は受け渡さない。

`cdc`（claude-devcontainer の略）は subtree の中の `.devcontainer/claude/bin/cdc` に置く。compose の構成（volume 名、サービス名）と同じ subtree で配るので、両者の版がずれない。clone 方式ではホストの作業ツリーが変わるのは承認したときだけなので、リポジトリにある `cdc` は常に信頼済みの版である。

README では、`~/.bashrc` などに次の関数を足すよう案内する。今いるリポジトリの `cdc` を呼ぶので、プロジェクトごとの設定は要らない。

```sh
cdc() { "$(git rev-parse --show-toplevel)/.devcontainer/claude/bin/cdc" "$@"; }
```

`cdc` 自身は、どこから呼ばれても対象のリポジトリを `git rev-parse --git-common-dir` で求める。`cdc merge --worktree` で作った worktree の中で呼ばれても、volume やコンテナは元のリポジトリのものを対象にするためである。

## 構成

```
ホスト                                    agent コンテナ
──────                                    ──────────────
repo/（信頼済み）                         /workspace  volume ws-<name>（clone、node_modules、build/）
  .devcontainer/.generated/  ── ro ──▶    /run/host/generated（初期化用の bundle を含む）
  .devcontainer/.nix-env/    ── ro ──▶    /run/host/nix-env
  cdc push   ── bundle ──▶ fetch           ~/.gradle   volume gradle-<name>
  cdc fetch  ◀── bundle ── HEAD            ~/.npm      volume npm-<name>
```

依存のキャッシュもエージェントごとの volume にする。複数のエージェントで共有するのは、必要になってからにする。

### ホストの生成物

`initializeCommand` がホストで生成するファイルのうち、エージェントの中で読むものがある。`bash-env.sh` は `.nix-env/env.rc` を、`wait-ready.sh` は `.generated/images.txt` を読む。今はどちらも `/workspace/.devcontainer/` 経由で読んでいるが、clone には入らない。

ホストの `.devcontainer/.generated/` と `.devcontainer/.nix-env/` を、それぞれ専用のパス（上の図では `/run/host/` の下。名前は仮）に read-only でマウントし、コンテナ内のスクリプトはそこから読む。今も `.devcontainer/` 全体を read-only で見せているので、エージェントに見えるものは増えない。

clone の中にも `.devcontainer/` はあるが、エージェントが書き換えられるので、コンテナ内のスクリプトはそこから設定を読まない。

### 初期化

専用のコマンドは作らず、起動の流れに組み込む。

- `initializeCommand`（`initialize.sh`）が、ホストの HEAD から `git bundle create` で bundle を作り、`.devcontainer/.generated/` に書き出す。
- agent の entrypoint は、`/workspace` が空なら、`/run/host/generated/` の bundle から clone する。空でなければ何もしない。

初回の `initializeCommand` の時点では agent のイメージがまだビルドされていないので、ホスト側で git の入った使い捨てコンテナを動かして clone することはできない。このため、clone は agent の entrypoint で行う。

### コンテナで実行するコマンド

`cdc` がエージェントのコンテナで実行するコマンド（`git rev-list`、`git bundle create`、`git fetch` など）は、すべて `docker exec --user node` で実行する。`docker exec` は指定しなければ root で動く。root では、clone の所有者と違うので Git が dubious ownership で失敗するうえ、エージェントが自分の `.git/config` に書いた `core.fsmonitor` や hook が、コンテナの capability を持ったまま動く。

### cdc push

`git bundle create - <ref>` をコンテナに標準入力で渡し、エージェント側で fetch する。rebase はエージェントに任せる。信頼済みのものを渡すだけなので、検査はしない。

### cdc fetch

1. コンテナの中で、ホストの HEAD からエージェントの HEAD までに新しいコミットがあるかを調べる（`git rev-list --count <ホストの HEAD>..HEAD`）。ホストの HEAD をエージェントが持っていなければ、全履歴を対象にする。
   - 0件（両者が同じか、エージェントの HEAD がホストの HEAD の祖先）：`refs/remotes/agent/main` を消し、「取り込むコミットはない」と表示して終える。`<ホストの HEAD>..HEAD` は空になり、`git bundle create` が `Refusing to create empty bundle` で失敗するので、bundle は作らない。ref を残すと、以前の fetch で取り込んだものが残る。エージェントが変更を取り消してホストと同じ HEAD に戻った場合、取り消した変更が `cdc merge` や `cdc pull` で再び取り込み候補になる。
   - 1件以上：履歴が分岐していても bundle を作って取り込む。ff できるかは `cdc merge` で判定する。
2. `docker exec agent git -C /workspace bundle create - <ホストの HEAD>..HEAD` の標準出力を、ホストで mktemp したファイルに受ける。bundle に含める先端は名前付きの ref でなければならないので、生のコミットIDではなく `HEAD` を渡す。
3. `git -c transfer.fsckObjects=true fetch --no-tags <bundle> +HEAD:refs/remotes/agent/main` で取り込む。ホストの作業ツリーと index は変わらない。
   - 更新するのは `refs/remotes/agent/main` だけにする。bundle の中身はエージェントが自由に作れる。`--no-tags` がないと、bundle に入っていたタグ（`refs/tags/*`）も取り込まれる（git 2.54.0 で確認した）。たとえば `main` という名前のタグが入ると、ブランチを動かさずに `main^{commit}` の解決先が変わり、`nix.ref = "main"` の評価にも影響する。
   - エージェントが rebase すると履歴が書き換わるので、強制更新（`+`）にする。

### cdc merge

fetch はせず、`refs/remotes/agent/main` を取り込む。

- 既定：`git merge --ff-only refs/remotes/agent/main`。ff できなければ（ホストの HEAD が先に進んでいる、履歴が分岐しているなど）git のエラーで止まる。エージェントに `cdc push` 後の rebase を頼む
- `--worktree`：`git worktree add ../<repo>.agent refs/remotes/agent/main`。メインの作業ツリーは変えない

### cdc pull

1. `cdc fetch` を行う。新しいコミットがなければ、そこで終える。
2. コミットの一覧（`git log --oneline HEAD..agent/main`）と、ファイルごとの変更行数（`git diff --stat HEAD agent/main`）を表示する。コミットメッセージとファイル名はエージェントが書いたものなので、端末の制御文字はエスケープして表示する。差分の中身は出さない。
3. 承認を求め（`[y/N]`）、承認したら `cdc merge` を行う。

## 後回しにするもの

- ホストへの依頼と結果返却の自動化。必要になったら、依頼は commit に含め、結果は `docker exec` の標準入力で返す形にし、共有ディレクトリは使わない
- 未追跡の秘密ファイル（`.env` など）を許可リストで渡すこと
- 複数のエージェントの並列実行と、キャッシュの共有
- エージェントの dev server をホストのブラウザで見るためのポート転送

## 未決事項

- `cdc` の実装言語（既存の scripts に合わせて Python か sh か）と、devcontainer CLI を経由するか `docker compose` を直接使うか
- Gradle の wrapper jar と配布物が今の proxy のポリシーで取得できるか
- 今の bind mount 構成からの移行手順
