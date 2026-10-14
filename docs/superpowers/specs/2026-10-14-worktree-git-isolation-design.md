# 作業ツリーを共有し Git を agent 専用にする

> この文書は当初の設計記録。`worktree`は`shared`へ改名し、従来のsharedモードは廃止した。専用の `accept` コマンドは削除し、通常の `git reset --mixed` に統一した。現在の取り込み手順は[利用手順](../../usage.md#ホストでレビューして取り込む)を参照。

## 目的と位置づけ

`[git] isolation` に第三の値 `worktree` を追加する。ホストの作業ツリーを `shared` と同じく read-write で共有しつつ、Git の管理ディレクトリは agent 専用の volume に置く。agent は自分の gitdir に対して add・commit・switch・stash を自由に行い、ホストの index・refs・config・hooks には一切触れない。ホストは `clone` と同じ `agent` リモートで agent のコミットを fetch し、レビュー後に `git reset --mixed` で HEAD と index だけを進める。

`shared_writable` は削除する。あの設定は `.git` を read-write にして既存の特定パスだけを read-only にする妥協で、[通信ルール](../../network-policy.md)が認めるとおり共有 Git の状態を隔離しない。`worktree` は同じ「agent が commit できる」を、ホストの `.git` を書き込み可能で開かずに実現する。失うのは「agent の commit が即座にホストの HEAD に乗る」ことだけで、それはホストの accept 一コマンドに置き換わる。

[共有・分離の設計](2026-10-13-workspace-modes-design.md)と[分離 clone の設計](2026-10-12-isolated-clone-design.md)を前提とし、両者の既定の動作は変えない。

## 設定と利用者から見た動作

```toml
[git]
isolation = "worktree" # 既定は "clone"。"shared" は .git を read-only で共有
```

値は `clone`、`shared`、`worktree` のみ許可する。`shared_writable` は未知の設定として起動前にエラーにする。環境変数からの上書きは引き続き追加しない。設定変更はコンテナの再作成で反映する。

| 項目 | `clone` | `shared` | `worktree` |
| --- | --- | --- | --- |
| `/workspace` | 専用 volume | ホストのリポジトリルートを read-write bind | 同左 |
| `/workspace/.git` | volume 内の独立した `.git` | ホストの `.git` を read-only bind | 専用 named volume `agent-git` |
| ホストの `.git` | 見えない | `/workspace/.git` に read-only | `/run/host/git` に read-only |
| `.devcontainer`、秘匿パス | 自前 | read-only bind、空マウント | 同左 |
| 作業開始時の内容 | 初回のみホストの HEAD をコピー | ホストの現在のファイル | ホストの現在のファイル。index は初回にホスト HEAD から組み立てる |
| agent の add・commit・switch・stash | 可 | 不可 | 可。自分の gitdir だけが変わる |
| agent からホストへ | `git fetch agent` | 編集が即時反映 | 編集は即時反映。commit は `git fetch agent` と accept |
| ホストから agent へ | `git push agent` | 不要 | agent が `git fetch /run/host/git` |
| コンテナ再作成 | 保存済み clone | ホストの作業ツリー | ホストの作業ツリーと保存済み `agent-git` |

## 構成

```text
worktree
ホストのリポジトリ
  ├─ 作業ツリー ──── read-write bind ───→ /workspace
  ├─ .git ────────── read-only bind ────→ /run/host/git
  └─ .devcontainer ─ read-only bind ────→ /workspace/.devcontainer
agent-git volume ──────────────────────→ /workspace/.git（agent の gitdir）
.generated/workspace-<commit>.git ─────→ 初回の objects と bootstrap ref

ホスト ←─ git fetch agent（ext:: docker exec git-upload-pack /workspace）
ホスト → git-agent.py accept <commit>（fetch 済みのコミットへ mixed reset）
```

モードで変える範囲は `shared` と同じく `/workspace` のマウント群、初期化、所有権の準備、`agent` リモートの利用可否に限る。

### 設定生成と Compose

`settings.py` は `worktree` で次のマウントを `.generated/workspace.compose.yaml` に生成する。

- ホストのリポジトリルートを `/workspace` に read-write bind。`shared` と同じ `check_shared_repository` を通す。
- `.devcontainer` を read-only bind。
- `agent-git` named volume を `/workspace/.git` に read-write で、`volume: {nocopy: true}` 付きで。Docker にホストの `.git` の内容を volume へコピーさせない。
- ホストの `.git` を `/run/host/git` に read-only bind。
- 秘匿パスは `shared` と同じ `shared_mounts` の処理を使う。

`agent-git` volume は core の `compose.yaml` の `volumes:` に `workspace` と並べて宣言する。名前は `{project}_agent-git` になり、モードの切り替えで削除しない。`worktree` 以外のモードでは宣言だけで接続しない。

`check_shared_mounts` は `worktree` でも動かし、`/workspace` が bind であること、`/workspace/.git` が `agent-git` volume であること、`/run/host/git` と `.devcontainer` が read-only であること、保護を上書きする追加マウントがないことを確認する。`settings.env` から `GIT_SHARED_WRITABLE` を外し、`compose.yaml` と `wait-ready.sh` の参照も消す。

### 起動処理

`initialize.sh` は `worktree` で `clone` と同じく `workspace-source.py` の seed 生成と `git-agent.py register` を実行する。

| 処理 | `clone` | `shared` | `worktree` |
| --- | --- | --- | --- |
| `workspace-source.py` の seed 生成 | 実行 | 実行しない | 実行 |
| `agent` remote の登録 | 実行 | 実行しない | 実行 |
| `wait-ready.sh` の Git 初期化 | clone | 検査のみ | 検査と gitdir の初期化 |

`workspace_mounts.py` の `prepare` は `worktree` で、`/workspace/.git`（volume のルート）だけを node の所有にし、依存マウントの扱いは `shared` と同じにする。ホストの作業ツリー、その親、`/run/host/git` には chown / chmod しない。

起動前検査（node で実行）は次を確認する。`.devcontainer` が read-only で書き込みできない。`/workspace/.git` が `/workspace` とは別のマウントで、node が書き込める。`/run/host/git` が read-only で、`HEAD`、`objects`、`refs` が読める。これらが満たされなければ、対象パスと設定箇所を示して停止する。

### gitdir の初期化

`clone-workspace.py` に `worktree` 用の処理を足す。入力は `clone` と同じ `/run/host/generated/workspace.json`（`commit`、`seed`、`branch`）と seed の bare repository である。

1. `/workspace/.git/HEAD` があり `rev-parse --verify HEAD^{commit}` が通れば、初期化済みとして「起動時の追従」へ進む。marker があれば中断した初期化として再開する。
2. marker `/workspace/.git/claude-bootstrap.json` に入力を記録する。
3. `git init` で `/workspace/.git` に gitdir を作る。既存の作業ツリーのファイルには触れない。
4. seed から `refs/heads/bootstrap` を fetch し、開始コミットの存在を確認する。
5. ホストのブランチ名で ref を作り（`update-ref refs/heads/<branch> <commit>`）、`symbolic-ref HEAD` を向ける。detached HEAD なら HEAD を直接コミットに向ける。
6. `git reset -q --mixed` で index だけを HEAD から組み立てる。`checkout` と `read-tree -u` は使わない。ホストの未コミット変更を壊すためである。
7. marker を消す。

`clone` にある「workspace が空であること」と「追跡パスと依存マウントの衝突」の検査は行わない。作業ツリーはホストのものであり、空ではないことが前提である。

### 起動時の追従

二回目以降の起動では、作業ツリーはホストの現在の状態だが、agent の HEAD は以前のままである。`workspace.json` は起動のたびにホストの現在の HEAD とブランチ名で書き直されるので、それを使って次のとおり追従する。

- agent の HEAD がホストのコミットの祖先（同じコミットを含む）なら、ホストのブランチ名の ref をホストのコミットに向け、`symbolic-ref HEAD` を切り替え、`git reset -q --mixed` で index を合わせる。ホストのコミットが agent の gitdir にないときは先に `/run/host/git` から fetch する。
- そうでなければ、agent に未取り込みのコミットがあるとみなして何も変えず、ホストと食い違っていることと、`git fetch /run/host/git <branch>` で取り込む手順を警告として出す。

「同期しない」という `clone` の原則の例外である。`worktree` では作業ツリーが常にホストのものなので、HEAD が古いままだとホストの通常のコミットが `git status` に巨大な差分として現れ、作業にならない。agent の作業を捨てる方向の変更は一切しない。

### ホスト側 `git-agent.py`

- `check_container` は label `worktree` を受け付ける。`/workspace` がホストのリポジトリルートの read-write bind で、`/workspace/.git` が `{project}_agent-git` volume の read-write マウントであることを確認する。`shared` は従来どおり拒否し、`clone` の検査は変えない。
- `worktree` への `git-receive-pack` は拒否する。`receive.denyCurrentBranch=updateInstead` の push は共有作業ツリーを書き換えるためで、ホストの変更は agent が `/run/host/git` から fetch する。`git-upload-pack` は `clone` と同じコマンドで動く。
- `accept <commit>` を追加する。引数は完全なコミット ID で、ブランチ名は受け付けない。順に次を確認し、どれかに失敗したら何も変えずに停止する。
  1. 実行中のコンテナが `worktree` であること。
  2. ホストの index に staged な変更がないこと（`git diff --cached --quiet`）。`reset --mixed` が消すためである。
  3. 指定コミットがローカルにあること（`cat-file -e`）。fetch は利用者が先に行う。
  4. ホストの HEAD が指定コミットの祖先であること（`merge-base --is-ancestor`）。そうでなければ、ホストのコミットがブランチから外れるので拒否し、agent に `/run/host/git` からの取り込みを依頼するよう案内する。
  5. 作業ツリーにルート以外の `.git`（ディレクトリまたはファイル）がないこと。symlink は辿らない。見つかったらパスを列挙して拒否する。
  6. `git reset -q --mixed <commit>` を実行し、`git status --short` を表示する。残った差分は agent が作業ツリーに置いたがコミットに入れなかったものである。

## モード切り替えとデータの保持

| 切り替え | 再作成後に使う内容 | 保持するもの |
| --- | --- | --- |
| `worktree` → `worktree` | ホストの作業ツリーと保存済み `agent-git` | agent のブランチと stash。HEAD は上記の追従規則に従う |
| `clone` → `worktree` | ホストの作業ツリー。`agent-git` が未作成ならホスト HEAD から初期化 | `workspace` volume は取り外したまま保存 |
| `worktree` → `clone` | 以前の clone の続き、または初期化 | `agent-git` volume は取り外したまま保存 |
| `shared` ↔ `worktree` | ホストの作業ツリー | 両方とも作業ツリーはホストのもの |

通常の切り替えに `down -v` や volume の削除を含めない。`clone` の成果を `worktree` に持っていくには、切り替える前に `git fetch agent` とレビュー・取り込みを行う。

## 共有モードの信頼の境界

`worktree` は `shared` の保護（`.devcontainer` の read-only、秘匿パス、ホスト `.git` への書き込み禁止）を維持したまま、agent に Git の全操作を許す。ホストの git が読む状態（index、refs、`refs/replace/`、`objects/info/alternates`、config、hooks）は agent から書けない。agent が自分の `.git/config` に `core.fsmonitor` や hooks を書いても、それを実行するのはコンテナ内の node であり、ホストの `git-upload-pack` 呼び出しは `clone` と同じ `docker exec --user node` の信頼経路を使う。ホスト側では fetch-pack と `transfer.fsckObjects` の扱いが `clone` と同じである。

残るものは次のとおり。

- 作業ツリー内でホストが実行するファイル（`flake.nix`、`.envrc`、`package.json`、`.vscode/` など）は `shared` と同様に守れない。
- agent は作業ツリーのサブディレクトリに `.git`（ディレクトリまたは `gitdir:` ファイル）を作れる。ホストでそのディレクトリに入って git を実行すると、その `.git/config` の `core.fsmonitor` や hooks が走る。bind mount では存在しないパスを予約できないので、ホストで git を実行する前に `accept` の検査、または同じ `find` を使う。これは作業ツリー共有に伴う課題で、`shared` にも同様に存在する。
- agent の `git switch` や `stash` は共有作業ツリーを書き換える。`.git` は壊れないが、ホストの作業中のファイルが消えたり差し替わったりする。agent 向けの SKILL.md で、ホストと同じブランチ上で作業し、switch・stash・checkout で作業ツリーを差し替えないよう指示する。
- tode の SCM ビューは agent の gitdir を見る。レビューは既存の手順どおりホストで行う。
- ホストが `git gc` しても agent の repo は壊れない。objects は seed からコピーした独立のものである。

## agent 向けの手順

SKILL.md に `worktree` の節を足す。

- `/workspace` はホストの作業ツリーで、編集は即時にホストへ届く。`.git` は自分専用で、add・commit・switch・stash はホストに影響しない。
- 作業はホストと同じブランチ、またはそこから切った `agent/<task>` ブランチで行い、switch・stash・checkout で作業ツリーの内容を差し替えない。
- コミットしたら、ブランチ名と完全なコミット ID を人間に伝える。人間はホストで fetch してレビューし、accept する。
- ホストが進んだら、`git fetch /run/host/git <branch>` して `git reset --mixed FETCH_HEAD`（作業ツリーは既にホストのもの）または merge / rebase する。`git push agent` はこのモードでは使えない。
- `/run/host/git` は read-only で、書き込みや設定変更を試みない。

## ホストでレビューして取り込む

usage.md の既存の手順に `worktree` 用を足す。fetch とレビューは `clone` と同じで、取り込みだけが違う。

```sh
git fetch --no-tags agent refs/heads/agent/task-name
agent_commit=$(git rev-parse --verify 'FETCH_HEAD^{commit}')
base_commit=$(git rev-parse --verify 'HEAD^{commit}')
git log --oneline "$base_commit..$agent_commit"
git diff "$base_commit" "$agent_commit"
python3 .devcontainer/claude/scripts/git-agent.py accept "$agent_commit"
```

`accept` を使わない場合の同じ手順も並記する。

```sh
git diff --cached --quiet
git merge-base --is-ancestor "$base_commit" "$agent_commit"
find . -name .git -not -path ./.git -prune -print
git reset -q --mixed "$agent_commit"
git status --short
```

`git merge --ff-only` は使えない。作業ツリーに agent の編集がある状態で checkout しようとして止まるためである。

## 検証すること

既存の `clone` と `shared` のテストを維持し、`shared_writable` のテストを削除して、次を追加する。

1. 設定で `worktree` が通り、`shared_writable` は未知の設定として拒否される。環境変数の上書きも拒否される。
2. `worktree` の Compose 解決結果で、`/workspace` が bind、`/workspace/.git` が `agent-git` volume で `nocopy`、`/run/host/git` と `.devcontainer` が read-only になり、`shared` と `clone` の結果は変わらない。
3. 初期化が作業ツリーのファイルに触れず、ホストと同じブランチ名で HEAD と index を作る。detached HEAD にも対応する。中断した初期化を再試行できる。
4. 起動時の追従で、agent の HEAD が祖先なら追従し、未取り込みのコミットがあれば据え置いて警告する。
5. `check_container` が `worktree` の label とマウントを受け付け、`shared` を拒否する。`worktree` への `git-receive-pack` が拒否される。
6. `accept` が staged 変更、未知のコミット、祖先でない HEAD、入れ子の `.git` で拒否し、正常時に HEAD と index だけが進んで作業ツリーが変わらない。
7. 実コンテナで、agent の commit がホストの `.git` を変えず、`git fetch agent` と `accept` でホストに届き、残った未コミット編集が `git status` に出る。agent から `/run/host/git` への書き込みが拒否される。`.devcontainer` と秘匿パスの保護、CapEff=0、ホストの `.git` と作業ツリーの所有権が不変であることを確認する。
8. `clone` → `worktree` → `clone` と切り替えて、両 volume が保持される。

実コンテナ試験には専用の一時リポジトリを使う。

## なぜこのアプローチを選んだか

- `shared_writable` の置き換えにした。四つ目として足すとコードと文書の分岐が増え、しかも `shared_writable` の方が隔離で劣る。
- `isolation` の第三の値にした。`shared` のサブオプションだと label と環境変数が二つに分かれ、`git-agent.py` の判定も二段になる。一つの値で決まる方が、コンテナの label からモードを一意に読める。
- objects は alternates でホストと共有せず、`clone` と同じ seed コピーを使う。alternates はディスクを使わずホストの全 ref が見えるが、ホストが base コミットを rebase で捨てて gc した時点で agent の repo が壊れる。seed は既に実装と検査があり、ホストの他ブランチは `/run/host/git` からの fetch で取れる。
- ホストの `.git` を `/run/host/git` に read-only で見せる。見せない案だとホストから agent への受け渡しが `git push agent` になり、`updateInstead` が共有作業ツリーを書き換える。read-only の local transport は読むだけなので安全で、`shared` が既に同じ内容を `/workspace/.git` に見せている。
- 初期化で `checkout` を使わない。作業ツリーはホストの未コミット変更を含むので、index だけを組み立てる。
- 起動時の追従を入れた。「同期しない」原則の例外だが、作業ツリーが常にホストのものである `worktree` では、HEAD が古いままだと通常の作業ができない。agent の作業を捨てる方向には動かさない。
- `accept` はコミット ID だけを受け付ける。fetch とレビューを手順から外さず、レビューした ID と取り込む ID を一致させるためである。
