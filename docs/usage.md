# 追加の利用手順

基本の起動・終了手順は[README](../README.md)を参照してください。この文書のホスト向けコマンドは、取り込んだリポジトリのルートで実行してください。認証情報は、ホストのClaude Codeのログインと`~/.config/claude-devcontainer/credentials.toml`から読みます。

`.devcontainer`を取り込まずに[`claude-devcontainer`](../README.md#devcontainerなしで使う)で起動した環境では、次のように読み替えてください。

| この文書の記述 | `claude-devcontainer`で起動した環境 |
| --- | --- |
| `.devcontainer/claude.toml`、`claude.local.toml` | `~/.config/claude-devcontainer/claude.toml`だけ。リポジトリ内の設定は読まない |
| `devcontainer up` | `claude-devcontainer up` |
| `devcontainer up --remove-existing-container` | `claude-devcontainer down`のあと`claude-devcontainer up` |
| `.devcontainer/claude/scripts/compose.sh ARGS` | `claude-devcontainer compose ARGS` |
| `.devcontainer/claude/scripts/code-server.sh` | `claude-devcontainer code-server` |
| `.devcontainer/.generated/` | `~/.local/state/claude-devcontainer/workspaces/<ID>/.generated/` |

`.devcontainer/compose.yaml`や`agent-setup.sh`への追加、`inject-credentials.py`・`load-images.py`・`acp-agent.py`の直接実行は、この方法では使えません。

## Gitの共有と分離

モードは`clone`、`shared`、`unsafe_shared`の3種類。旧`worktree`設定は`shared`に変更する。従来のshared（ホストの`.git`を直接read-onlyで参照する方式）は廃止した。更新後はコンテナを再作成する。

`.devcontainer/claude.toml`、またはホストだけの`.devcontainer/claude.local.toml`で指定する。省略時は従来どおり`clone`。ローカル設定が優先される。

```toml
[git]
isolation = "clone" # "shared" にするとホストの作業ツリーを共有
```

| 設定 | 作業場所 | Gitと変更の受け渡し |
| --- | --- | --- |
| `clone` | 独立した永続volume。初回のみホストHEADをコピー | 独立した`.git`。ホストの`.git`は`/run/host/git`にread-only。ホストから`git fetch/push agent` |
| `shared` | ホストの作業ツリーを`/workspace`へread-write mount | `/workspace/.git`はagent専用のvolume。ホストの`.git`は`/run/host/git`にread-only。ホストから`git fetch agent`と`git reset --mixed` |
| `unsafe_shared` | ホストの作業ツリーをread-write mount | ホストのindex・refs・objectsも直接共有。設定・hooksなどは保護 |

設定変更後はコンテナを再作成する。

```sh
devcontainer up --workspace-folder . --remove-existing-container
```

切り替えは保存先の選択だけで、自動同期は行わない。`clone → shared → clone`では以前のcloneのブランチと未コミット変更が残る。cloneが未作成なら、その時点のホストHEADから初期化する。sharedの`agent-git` volumeも同様に、他のモードの間は取り外したまま残る。Node/GradleのCoWは共通で、切り替えてもキャッシュを保持する。通常の切り替えで`down -v`やvolumeの削除を行わない。

sharedはルートに実体の`.git`ディレクトリがある通常リポジトリに対応する。linked worktree、外部git-dir、object alternatesなどは`clone`を使う。ホストのUID/GIDに`agent.uid`/`agent.gid`を合わせ、追加の依存mountの親ディレクトリはホストで事前に作る。起動処理はホストの作業ツリーや`.git`をchown/chmodしない。

```toml
[git]
isolation = "shared"
```

sharedでは`/workspace/.git`に`agent-git` named volumeを重ね、ホストの`.git`は`/run/host/git`にread-onlyで置く。初回は`clone`と同じローカルコピー（`.generated/workspace-<commit>.git`）からagentの`.git`を作り、ホストと同じブランチ名でHEADとindexだけを組み立てる。作業ツリーのファイルには書かないので、ホストの未コミット変更はそのまま残る。agentはadd・commit・switch・stashを自分の`.git`に対して行い、ホストのindex・refs・設定・hooksには書けない。`.devcontainer`はread-onlyで保護する。このディレクトリ自体の開発にはcloneを使う。秘匿対象のうち追跡されているファイルは、agentからは空に見えるので、agentのindexで`skip-worktree`を付ける。`git status`に出ず、`git add`も拒否されるため、空になった版がcommitに入らない。

agentのcommitはホストへ自動では乗らない。ホストで[fetchしてレビューし](#ホストでレビューして取り込む)、`git reset --mixed`でHEADとindexをそのコミットへ進める。agentへの`git push agent`は使えず、ホストの新しいコミットはagentが`git fetch /run/host/git <branch>`で取り込む。コンテナの再作成時にagentの未取り込みのコミットがなければ、agentのHEADはホストの現在のブランチとコミットに自動で追従する。あれば据え置いて警告を出す。

agentの`git switch`や`stash`は共有作業ツリーのファイルを差し替える。agentにはホストと同じブランチ、またはそこから切った`agent/<task>`で作業し、作業ツリーを差し替えないよう指示している。ホストでサブディレクトリに入ってGitを実行する前に、agentが作った`.git`がないことを`find . -path ./.git -prune -o -name .git -print`で確認する（[隔離の範囲](network-policy.md#sharedで共有されるもの)）。

秘匿対象は`.devcontainer/claude.local.toml`の`git.shared_hidden_paths`で指定する。既定は空のリストで、`.secrets`などの名前を特別扱いしない。リポジトリ内を指す`sumi.secrets`は別途自動で隠す。ディレクトリは空のread-only tmpfs、ファイルは空ファイルのread-only bindで覆う。

```toml
[git]
isolation = "shared"
shared_hidden_paths = [".env", "private/credentials"]
```

`shared_hidden_paths`はリポジトリルートからの相対パスのリストで、ワイルドカードや`..`は使えない。指定したパスは生成時に存在する必要がある。無効なsumiの未作成ファイルはスキップする。ローカル設定のリストは共有設定のリストを置き換え、`[]`なら指定対象なしになる。対象とその親がsymlinkなら起動を拒否する。重複・入れ子の指定は親のマウントにまとめる。`.git`や、別の管理用パスでも参照する`.devcontainer/.generated`・`.devcontainer/.nix-env`は隠せないので、そこには秘密を置かない。

対象は`generate`時に決まり、反映にはコンテナの再作成が必要になる。起動後に作った、まだ覆われていない場所の秘密は隠せない。秘匿対象外の未追跡・ignoredファイルは共有される。ホストでは`git diff`で追跡ファイルの変更をレビューし、`git status --ignored`でignoredファイルも確認してから実行する。[隔離の範囲](network-policy.md#sharedで共有されるもの)も参照する。

既存の利用先を更新した場合は、`.devcontainer/compose.yaml`のincludeを次の形に変更する。コアのsubtree更新だけでは、利用先が所有するこのファイルは更新されない。

```yaml
include:
  - path: [claude/compose.yaml, .generated/workspace.compose.yaml]
    env_file: [.generated/networks.env, .generated/settings.env]
```

### Gitも直接共有する unsafe_shared

```toml
[git]
isolation = "unsafe_shared"
```

作業ツリーに加え、index・HEAD・refs・objects・reflogをホストとRW共有する。add・commit・reset・switchはホストのGit状態を直接変更する。起動時にindexを初期化せず、既存のステージ済み変更も保持する。agentリモートの登録・fetch/pushによる受け渡しは不要。ACPも利用できる。

rebase-todoを含む`.git/rebase-merge`・`.git/rebase-apply`・`.git/sequencer`もRW共有し、履歴編集を制限しない。ホストで`git rebase --continue`すると、共有されたtodoの`exec`もホストで実行される。これはunsafe_sharedで許容する共有範囲に含まれる。

`.devcontainer`と既存の秘密マスクは維持する。`.git/config`と`.git/config.worktree`をread-onlyにし、`.git/hooks`、`.git/info`、`.git/objects/info`は空のread-only mountで覆う。submoduleのGitディレクトリ`.git/modules`は、それぞれのconfigとhooksをホストのGitが使うのでread-onlyにする。agentはsubmoduleの中でcommitできず、新しいsubmoduleも初期化できない。不在の保護用ディレクトリと空のconfig.worktreeは設定生成時に作成する。独自のcore.hooksPathやinclude/includeIf設定は拒否する。通常リポジトリのみ対応し、linked worktreeやalternatesは使えない。

このモードはホストのGit状態を隔離しない。agentによるindexのフラグ操作や履歴変更もホストへ反映される。ホストとagentの同時Git操作を避ける。秘匿した追跡ファイルは空または削除として見えるが、ホストのindexへskip-worktreeを付けることはしないため、包括的なgit addで取り込まないこと。保護対象以外の共有ファイルをホストが実行するリスクも残る。

## 初回起動（clone、shared）

`devcontainer up` で起動する。ホストのローカルHEADとその履歴をコピーし、cloneでは空のworkspace volumeにホストと同じブランチ名でcheckoutする。sharedでは同じコピーから`agent-git` volumeにHEADとindexだけを作り、ホストの作業ツリーには書かない。ホストがdetached HEADならコンテナ側もdetached HEADにする。ブランチ名を合わせるのは初回作成時だけで、以降はコンテナ側のブランチを維持する。事前のpushやコンテナ内での手動cloneは不要である。未pushのコミットも使えるが、未コミット・未追跡ファイルはコピーしない。

cloneでは、ホストでcheckout済みのsubmoduleも、親が記録したコミットのものをホストからコピーしてcheckoutする。agentのsubmoduleのURLは、remoteと同じ規則でHTTPSにしたもの（proxyが許可しないホストなら`.gitmodules`のまま）にするので、以後は`git submodule update`などでproxy経由で取得できる。ホストで未初期化のsubmoduleや、agentのHEADが別のコミットを記録しているsubmoduleは空のまま残る。入れ子のsubmoduleは辿らない。起動のたびに、空で未初期化のsubmoduleだけを対象にする。

sharedでは、submoduleのファイルはホストのものだが、その`.git`ファイルが指す`.git/modules/<name>`はagent専用のvolumeにあるため、そのままではリポジトリ全体でGitが失敗する。起動時に、ホストでcheckout済みのsubmoduleについて、ホストのコピーから`.git/modules/<name>`を作り、HEADを親が記録したコミットに、indexをホストのファイルに合わせる。ファイルには書かない。URLはcloneと同じ規則にする。一度作ったものは作り直さない。`.git`ファイルが`.git/modules/<name>`以外（絶対パスなど）を指すsubmoduleや、submoduleの中に`.git`ディレクトリを持つ古い形式のものは対象外。

ホストのremoteのうち、接続先が`proxy.hosts`にあるもの（GitHubの`origin`など）は、agentにも同じ名前で登録する。agentは`git fetch origin`で最新のupstreamを取り込める。URLはHTTPSにし、`user:token@`のような認証情報は除く。SSHのURL（`git@github.com:o/r.git`）は同じパスのHTTPSに変える。`file://`やローカルパス、`ext::`、proxyが許可しないホストのremote、`host`と`agent`という名前のremoteは引き継がない。登録は起動のたびに行い、agentに同じ名前のremoteがあれば変更しない。agentからのpushはproxyが拒否する（テンプレートの`proxy.hosts`は`git-receive-pack`を許可しない）。不要なら`claude.toml`で`git.inherit_remotes = false`にする。

## 受け渡し用リモート（clone、shared）

起動処理がホストに`agent`リモートを自動登録する。接続先は起動中のコンテナ内の`/workspace`であり、GitHubなどの外部リポジトリは介在しない。既存の`origin`は変更しない。同名の`agent`が別用途で存在する場合は上書きせず停止するので、既存のリモートを別名にしてから起動する。

Git標準の`ext`転送とホスト側の`git-agent.py`を使い、Dockerの標準入出力をコンテナ内の`git-upload-pack` / `git-receive-pack`につなぐ。どちらも`node`で実行する。SSHサーバーや公開ポートは不要で、接続時にGitを実行したworktreeとそのコンテナを見つけ直すため、複数worktreeや再作成にも追従する。このホストリポジトリだけに`protocol.ext.allow=user`を設定する。`user`は、利用者が直接実行したGitのコマンドにだけ`ext`転送を許し、submoduleのcloneのようにGitが自分で実行するコマンドには許さない。接続にはコンテナが起動している必要がある。

エージェントは引き継いだブランチで作業できる。必要なら次のように別の作業ブランチを作る。コミット後はブランチ名とコミットIDを人間に伝える。pushは不要。

```sh
git switch -c agent/task-name
# 編集、テスト、commit
```

コンテナにはホストへ書き戻すためのリモートやホストのDocker socketを渡さない。受け渡しの操作はホストから行う。

## ホストでレビューして取り込む

```sh
git fetch --no-tags agent refs/heads/agent/task-name
agent_commit=$(git rev-parse --verify 'FETCH_HEAD^{commit}')
base_commit=$(git rev-parse --verify 'HEAD^{commit}')
git log --oneline "$base_commit..$agent_commit"
git diff "$base_commit" "$agent_commit"
```

`--no-tags` とブランチを指定した fetch により、レビューと無関係なタグを取り込まない。

レビューでは、取り込んだ後にホストで実行されるファイルの変更を必ず確認する。そのうち場所の決まっているものは、次のコマンドで一覧できる。

```sh
git diff --stat "$base_commit" "$agent_commit" -- .devcontainer .gitmodules flake.nix flake.lock .envrc .vscode .claude .mcp.json
```

| ファイル | ホストでの扱い |
|---|---|
| `.devcontainer/`の全体 | `devcontainer up`のたびに、`devcontainer.json`の`initializeCommand`と、そこから呼ぶスクリプト（`claude/scripts/`など）をホストで実行する。`agent-setup.sh`は、agentのimageのビルドでrootとして実行する |
| `.devcontainer/compose.yaml` | ホストのディレクトリやsocketをagentへmountできる。`cap_add`や`privileged`でagentの隔離を外せる |
| `.devcontainer/claude.toml` | `proxy.hosts`は、ホストの秘密をどの接続先へ付けるかを決める。`docker.images`はホストがpullするイメージを、`nix`はホストで評価するflakeを、`sumi.secrets`と`proxy.ca`はコンテナへmountするホストのファイルを決める |
| `flake.nix`、`flake.lock` | `nix.enabled`なら、`devcontainer up`のたびにホストで評価する。ホストの`nix develop`でも実行する |
| `.envrc` | direnvが、ホストでディレクトリに入ったときに実行する |
| `.vscode/` | ホストのVS Codeが、`tasks.json`のタスクや、設定に書かれたコマンドを実行する |
| `.claude/`、`.mcp.json` | ホストのClaude Codeが、hookやMCPサーバーとして実行する |
| `.gitmodules` | ホストの`git submodule update`が取得するURLを決める |
| `package.json`、`build.gradle`、`gradle/wrapper/`、`Makefile`、`.husky/`など | ホストでビルド、テスト、`git commit`などを実行したときに動く |

承認したら、同じコミットIDを取り込む。cloneではfast-forwardでmergeする。

```sh
test "$(git rev-parse HEAD)" = "$base_commit" && git merge --ff-only "$agent_commit"
```

sharedでは作業ツリーに既にagentの編集があるため、`git reset --mixed`でファイルを保持したままHEADとindexだけを進める。

```sh
test "$(git rev-parse HEAD)" = "$base_commit" &&
  git diff --cached --quiet &&
  git merge-base --is-ancestor "$base_commit" "$agent_commit" &&
  git reset -q --mixed "$agent_commit" &&
  git status --short
```

レビュー開始時からHEADが変わっていないこと、stagedな変更がないこと、取り込むコミットへfast-forwardできることを確認してから実行する。いずれかの確認に失敗すると、HEADとindexは変更しない。残った差分は取り込んだコミットに含まれない作業ツリーの変更である。fetch済みなら、コンテナが停止していても実行できる。

レビュー中にエージェントが追加でcommitしたり、IDE が background fetch したりしても、変数に保存したIDは変わらない。ホストの HEAD が変わった場合は差分を確認し直す。ff できなければホストの変更を push し、エージェントに rebase を依頼して、更新後のコミットを再びレビューする。

ホストでテストするために別 worktree へ取り出す場合も、レビュー済みのIDを指定する。

```sh
git worktree add --detach ../repo.agent "$agent_commit"
```

取り出したファイルは IDE やビルドツールから実行されるため、別 worktree への取り出しもレビューのあとに行う。`git pull` は fetch と取り込みを続けて行うので、このレビュー手順では使わない。

## ホストの変更を渡す

sharedでは、ホストの`.git`がコンテナの`/run/host/git`にread-onlyで見えている。agentがコンテナ内で取り込む。

```sh
git fetch /run/host/git main
git reset --mixed FETCH_HEAD   # 作業ツリーは既にホストのもの
```

cloneでも、ホストの`.git`がコンテナの`/run/host/git`にread-onlyで見えている。agentに「同期して」と頼めば、コンテナ内で取り込む。

```sh
git fetch host
git rebase host/main   # または git merge host/main
```

remote `host`は起動時に`/run/host/git`を指すように登録する。同名のremoteが既にあれば変更しない。linked worktreeではリポジトリ共通の`.git`を置くので、全ブランチが見える。objectsをalternatesから借りているリポジトリでは`/run/host/git`を置かない。ホストとagentのUIDが違うとGitがdubious ownershipで拒否するので、`agent.uid`/`agent.gid`をホストの所有者に合わせる。

ホストからコンテナの受け取り用ブランチへpushしてもよい。

```sh
git push agent HEAD:refs/heads/host/main
```

エージェントはコンテナ内で`git merge host/main`または`git rebase host/main`を実行する。

コンテナでcheckout中のブランチへも、`-f`を含めてpushできる。次の例は、コンテナで`main`をcheckoutしている場合である。

```sh
git push -f agent HEAD:main
```

このときGitは、コンテナの作業ツリーとインデックスもpushしたコミットに合わせて更新する（`receive.denyCurrentBranch=updateInstead`）。エージェントが作業している途中でも、未コミットの変更がなければ作業ツリーのファイルは書き換わる。コンテナの作業ツリーかインデックスに未コミットの変更があるとき、またはpushしたコミットが未追跡のファイルを上書きするときは、Gitがpushを拒否する。そのため、コンテナの未コミットの作業を自動で上書きしない。`-f`でブランチからたどれなくなったコンテナのコミットは、コンテナの`git reflog`に残る。

## 依存の共有と更新

`node_modules`などの依存は、組み込みでは共有しません。ホストの依存をagentでも使うには、`claude.toml`の`agent.mounts`にCoW（`:cow`）で書きます。`/workspace/<パス>:cow`と書くと、リポジトリの同じパスのディレクトリをlower、agent専用の永続ディレクトリをupper/workとするoverlayになります。リポジトリにそのディレクトリがなければマウントせず、ホストにディレクトリも作りません。このため、全リポジトリ共通の`~/.config/claude-devcontainer/claude.toml`にも書けます。

```toml
[agent]
mounts = ["/workspace/node_modules:cow", "/workspace/packages/app/node_modules:cow"]
```

agentの書き込み・削除はupperに残り、ホストやpnpmのstoreには書き戻しません。upper/workは`$XDG_STATE_HOME`（未指定なら`~/.local/state`）の`claude-devcontainer/cow/`以下に、リポジトリとマウントごとに分かれて置かれます。overlayのオプションとして表現できない空白、カンマ、コロン、等号、バックスラッシュ、引用符を含むパスは拒否します。`/workspace`の下には`:cow`だけを書けます（`.git`と`.devcontainer`の下は不可）。

npm、通常のpnpm、Yarnのnode_modulesモードを対象にします。Yarn PnPや外部のvirtual storeは自動対応しません。native addonにはホストとコンテナのNode・libc等の互換性が必要です。新しい依存をオンライン取得する仕組みではありません。Pythonの`.venv`など、ほかの依存ディレクトリも同じ書き方で共有できます。

初回cloneは実際の依存のマウントとその祖先ディレクトリだけを許容します。普通の既存ファイルや、取得コミットの追跡パスとマウントの衝突は拒否します。

以前は、ルートの`node_modules`を組み込みでCoWにしていました（ホストに`node_modules`がなければ空のディレクトリを作っていました）。引き続き使うなら上の`mounts`を書いてください。以前のupperの変更は引き継がれません。不要になった`~/.local/state/claude-devcontainer/node-modules/`は消して構いません。

CoWはスナップショットではないため、mount中にホストのlowerを変更しないでください。依存を更新するときは次の順です。

1. agent側の依存への変更に残すものがあれば退避する。
2. ホストで`.devcontainer/claude/scripts/compose.sh down`を実行し、コンテナを停止・削除してoverlayをアンマウントする。`--volumes`は付けない。
3. ホストで使っているpackage managerにより依存を更新する。
4. `~/.local/state/claude-devcontainer/cow/`の下の、そのリポジトリとマウントのupper/workディレクトリだけを削除する。起動時に空で作り直される。overlayfsが作るwork内のroot所有の領域の削除には、管理者権限が必要な場合がある。
5. `devcontainer up`で起動する。workspace volumeとGradleのupperは削除しない。

通常の再起動・再作成はupperを保持します。Gradleは既存の`~/.gradle`のCoWを使い、`./gradlew --offline jar test`を実行します。ホストでGradleのキャッシュを更新する場合も、先にoverlayをアンマウントしてください。

### 読み取り専用のバインドで共有する

CoWの代わりに、ホストの`node_modules`を読み取り専用でバインドすることもできます。agentは依存を変更できず、`pnpm install`などの書き込みは`Read-only file system`（`EROFS`）で失敗します。一方で、ホストで依存を更新するときに、agentを止めたりupper/workを消したりする必要はありません。ただし、更新の途中の`node_modules`にはまだ入っていないパッケージや書きかけのファイルが含まれることがあるので、その間はagentでツールを実行しないでください。

ツールによっては、実行時に`node_modules`の下へキャッシュや一時ファイルを書き込みます。たとえばVite、Vitest、`@nuxt/test-utils`、jiti、Prettier、vue-tscは、`node_modules/.cache`、`node_modules/.vite-temp`、`node_modules/.vue-global-types`のいずれかへ書き込みます。書き込み先はツールと設定によって違うので、使うコマンドをagentで実行し、`Read-only file system`で失敗するディレクトリを確かめてください。それらのディレクトリにtmpfsなどの書き込めるマウントを追加します。tmpfsの内容はコンテナを再起動すると消えます。

```yaml
services:
  agent:
    volumes:
      - type: bind
        source: /absolute/repo/packages/app/node_modules
        target: /workspace/packages/app/node_modules
        read_only: true
        bind: {create_host_path: false}
    tmpfs:
      - /workspace/packages/app/node_modules/.cache:mode=1777
      - /workspace/packages/app/node_modules/.vite-temp:mode=1777
      - /workspace/packages/app/node_modules/.vue-global-types:mode=1777
```

Dockerは読み取り専用のバインドの中にマウント先を作れないので、tmpfsのマウント先のディレクトリはホストで先に作っておきます。ホストで`node_modules`を作り直してこれらのディレクトリが消えたときは、もう一度作ってから起動します。

バインドするディレクトリの所有者は、`agent.uid`と`agent.gid`に合わせてください（[UIDとネットワークを変える](../README.md#uidとネットワークを変える)）。entrypointは依存をマウントしたディレクトリの所有者をagentに変えますが、読み取り専用のマウントでは変えられません。所有者が違うと、agentのコンテナは起動時にentrypointのエラーで終了します。

## 認証情報を更新する

Claudeの認証には、ホストのClaude Codeのログインを使います。ホストで`claude`にログインしていれば、設定は要りません。ログインし直した場合や、`~/.config/claude-devcontainer/credentials.toml`で`proxy.hosts`が参照する秘密（`GITHUB_TOKEN`など）を変えた場合は、注入スクリプトを再実行してください。proxyやagentの再起動・再ビルドは不要です。

```sh
python3 .devcontainer/claude/scripts/inject-credentials.py
```

proxyは1環境につき1組の認証値を持ち、次の要求から新しい値を使います。認証値はproxy専用tmpfsに保存され、置き換えるかproxyを停止するまで有効です。ファイルはproxyのUID 1000（agentのUIDの設定とは無関係）が所有する0400で、agentやDinDからは読めません。

`devcontainer up`で起動すると、注入スクリプトは環境が動いている間ホストで動き続けます。proxyやagentが作り直されれば、その都度注入し直します。起動から10分間は、環境が消えても終了しません。`devcontainer up --remove-existing-container`はagentを消してからイメージをビルドするので、その間も待ち続けて新しいagentに注入します。

### ホストのClaude Codeのログイン

注入スクリプトは、ホストのClaude Codeのログイン情報（`$CLAUDE_CONFIG_DIR`、なければ`~/.claude`の`.credentials.json`）からaccess tokenを読み、proxyへ渡します。別の場所のファイルを使う場合は、`~/.config/claude-devcontainer/credentials.toml`の`[claude]`に`credentials_file = "<パス>"`と書いてください。

- agentには本物のトークンを見せません。
- 期限の5分前になると、ホストからAnthropicへ直接、トークンの更新を要求し、新しいトークンをファイルへ書き戻してからproxyへ渡し直します。ホストのClaude Codeと同じロックを取ってからファイルを読み直すので、ホストのClaude Codeと同時に更新して壊すことはありません。ホストのClaude Codeが先に更新した場合は、その値を使います。
- 書き戻すのは`claudeAiOauth`のaccess token、refresh token、期限だけです。
- 更新に失敗した場合は、今のトークンを使い続け、30秒後にやり直します。ホストで`claude /login`し直せば、新しいログインが使われます。
- agentのClaude Codeには、ログインのscope、subscriptionの種類などを`/etc/claude-code/managed-settings.d/60-claude-login.json`で渡します。トークンは含みません。
- 更新の要求の形式は、Claude Code 2.1.295の実装に合わせています。Claude Codeがこれを変えると、更新は失敗します。その場合も、ホストのClaude Codeを使っていれば、その更新した値を読み直して動き続けます。

`claude setup-token`のトークン（`CLAUDE_CODE_OAUTH_TOKEN`）は使いません。推論用のscopeしか持たず、claude.aiのコネクタが使えないうえ、期限切れを自動で更新できないためです。設定ファイルに残っていると、注入スクリプトが警告を出して無視します。

### API keyを使う

ログインの代わりに、Claude ConsoleのAPI keyも使えます。`~/.config/claude-devcontainer/credentials.toml`の`[claude]`に`api_key = …`（値か`{ command = "…" }`）と書いてください。API keyでは、Claude CodeがArtifactsとclaude.aiのコネクタを使えません。API keyは自動では更新しないので、Consoleで更新したら注入スクリプトを再実行してください。

注入コマンドは起動済みの環境だけを対象にします。環境がない場合や対象が複数ある場合は失敗するので、先に`devcontainer up`を実行してください。`proxy.hosts`が参照する秘密は、`~/.config/claude-devcontainer/credentials.toml`に書いたものだけを渡し、環境変数からは取りません。Claudeの認証は環境変数`ANTHROPIC_API_KEY`か`CLAUDE_CREDENTIALS_FILE`でも指定でき、その場合はファイルの`[claude]`より優先します。`[claude]`も`[workspaces."パス".claude]`でワークスペースごとに変えられます。

認証値はすべてのagentプロセスの通信に共通です。すでに開いているVS Codeのターミナルや通常の`devcontainer exec`からも同じ認証で通信でき、利用者ごとの分離はありません。agentのClaude Codeは起動時にダミーのOAuthトークンを使い、proxyが本物のトークンまたはAPI keyへ置き換えます。

## DockerとTestcontainers

DinDは`.devcontainer/claude.toml`の`[docker]`に`enabled = true`と書いた場合だけ起動します。作業用コンテナから使うDockerは、専用のDinDコンテナで動作します。registry、外部DNS、ホストサービスには接続できません。必要なイメージは、同じ`[docker]`の`images`に`名前:tag`の形で並べてください。

```toml
[docker]
enabled = true
images = ["testcontainers/ryuk:0.14.0", "postgres:16"]
```

`devcontainer up`のたびに、イメージ読み込みスクリプトがホストにないイメージをホストでpullし、DinDへ読み込みます。

- イメージ読み込みスクリプトは、ホストにすでにあるイメージをtagが動いていてもpullし直しません。新しくしたいときは、ホストで`docker pull`してから`up`してください。
- DinDのデータはvolumeに残るので、送るのは初回と、ホストのイメージが変わったときだけです。
- コンテナの起動時は、読み込みが終わるまで`up`が待ちます。失敗したイメージは警告に出ます。詳しくは`~/.local/state/claude-devcontainer/load-images.log`を見てください。
- `up`の後に`images`を変えた場合は、ホストで`.devcontainer/claude/scripts/load-images.py`を実行すると反映できます。

イメージ読み込みスクリプトはホスト側の`claude.toml`から生成した一覧を使います。agentが編集できるclone内の一覧は使いません。`claude.toml`の変更をホストへ取り込むとpull先も変わるため、コミットのレビュー時に確認してください。

投入後は、agentのシェルから`docker run`や`docker build --network=none`を実行できます。buildには投入済みのbase imageとローカルにある依存ファイルを使ってください。Gradleは`--offline`で実行し、その他のツールも事前に用意したキャッシュや依存ファイルを使ってください。ビルド中のオンラインpackage installには対応していません。

agentにツールを追加する場合は、[`.devcontainer/agent-setup.sh`](../.devcontainer/agent-setup.sh)に導入手順を書いてください。ホストでagentのimageをビルドする際にrootで実行されます。すべてのリポジトリで使うツールは、ホストの`~/.config/claude-devcontainer/agent-setup.sh`に書けます。こちらが先に実行されます。

コンテナのTCPポートは、`docker run -p 8080`または`-p 18080:8080`のように、既定の`0.0.0.0`で公開してください。agentからは、`127.0.0.1:公開ポート`に接続できます。loopbackだけで公開したポートは中継されません。Docker API用の2375番ポートも公開先としては使えません。

Testcontainersでは、テスト対象とRyukのイメージを事前に投入してください。以下の接続設定はComposeで設定済みです。

```sh
DOCKER_HOST=tcp://127.0.0.1:2375
TESTCONTAINERS_HOST_OVERRIDE=127.0.0.1
TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/run/user/1000/docker.sock  # 1000はagentのUID
```

### DinDのイメージを複数のリポジトリで共有する

DinDのデータは、既定ではリポジトリごとのvolumeに置かれ、同じイメージもリポジトリの数だけ保存されます。`docker.shared_base`に名前を書くと、同じ名前を書いたリポジトリでイメージの置き場（ベース）を共有します。各リポジトリのDinDは、ベースの上にリポジトリごとの書き込み層を重ねて（copy-on-write）動くので、同時に起動でき、互いのコンテナやイメージの変更は見えません。

```toml
[docker]
enabled = true
shared_base = "common"
```

全リポジトリ共通の`~/.config/claude-devcontainer/claude.toml`か`claude.local.toml`に書くのが便利です。`docker.enabled`が`false`のリポジトリでは無視されます。

ベースへのイメージの読み込みは、ホストで次のように実行します。そのリポジトリで一度`devcontainer up`してDinDのimageを作ってから、ベースを使うすべての環境を停止して実行してください。

```sh
.devcontainer/claude/scripts/dind-base.py                    # docker.imagesを読み込む
.devcontainer/claude/scripts/dind-base.py postgres:16 nginx  # 指定したイメージを読み込む
```

- ベースは`~/.local/state/claude-devcontainer/dind-base/<名前>/data`、リポジトリごとの書き込み層は`~/.local/state/claude-devcontainer/dind/`の下に置きます。`up`のたびに`docker.images`のうちベースにないイメージは、書き込み層へ読み込みます。
- ベースを使うDinDは、データをfuse-overlayfsで保存します（カーネルのoverlayはoverlayの上に書き込み層を置けないため）。共有しない場合より遅くなります。共有に切り替えると、DinDのデータは新しい置き場から始まります。
- ベースを使うリポジトリの`agent.uid`は揃えてください。ベースを作ったときと異なるUIDでは`up`がエラーになります。
- ベースは書き込み層の下にあるため、使っている環境の動作中は変更しないでください。`dind-base.py`は、ベースを使うDinDが動いていれば止まります。
- ベースへ後から追加したイメージは、DinDがまだイメージ一覧を書き込んでいないリポジトリにしか見えません。見えないリポジトリは、停止して`~/.local/state/claude-devcontainer/dind/`の下のそのリポジトリの書き込み層を消すと、ベースから始め直せます（そのリポジトリのDinDのデータは消えます）。ベースからイメージを消すことはできません。

## ACPクライアントから使う

ホストのEmacs、Neovimなどから、コンテナ内の[claude-agent-acp](https://github.com/agentclientprotocol/claude-agent-acp)へ接続できます。[`git.isolation = "shared"`](#gitの共有と分離)または`unsafe_shared`が前提です。agentの`/workspace`がホストの作業ツリーそのものになるので、エディタとagentが同じファイルを指せます。`.devcontainer/claude.toml`か`claude.local.toml`で両方を有効にしてください。

```toml
[git]
isolation = "shared"

[acp]
enabled = true
version = "0.90.0"
```

`acp.enabled`の既定値は`false`で、無効ならACPのパッケージをダウンロード・インストールしません。`clone`のまま`acp.enabled = true`にすると、`devcontainer up`が設定エラーで止まります。`version`は省略でき、既定値は`0.90.0`です。完全なバージョン番号で指定してください。`latest`などのタグや`^0.90.0`などの範囲指定は受け付けません。別バージョンと同梱のClaude Codeとの互換性は、利用する版で確認してください。有効化・バージョンの変更後は、`devcontainer up --remove-existing-container`でimageを作り直します。

クライアントを問わず、次のコマンドをACPのstdioサーバーとして登録してください。ホストに必要なのはPython 3とDocker CLIです。

```sh
/absolute/path/to/project/.devcontainer/claude/scripts/acp-agent.py
```

このスクリプトが置かれたリポジトリの、起動済みのagentへ接続します。別の場所にあるスクリプトを共用する場合は、引数に`--workspace-folder /absolute/path/to/project`を付けてください。接続時には、実行中のコンテナがsharedで作られ、このリポジトリの作業ツリーをmountしていることを`git fetch agent`と同じ方法で確かめます。設定ファイルを書き換えただけで、コンテナがcloneのままなら接続を拒否します。

### Emacs agent-shell

[agent-shell](https://github.com/xenodium/agent-shell)のClaude用コマンドを設定します。次のパスは、ホストのリポジトリの絶対パスに置き換えてください。

```elisp
(with-eval-after-load 'agent-shell-anthropic
  (setq agent-shell-anthropic-claude-acp-command
        '("python3" "/absolute/path/to/project/.devcontainer/claude/scripts/acp-agent.py")
        agent-shell-anthropic-authentication
        (agent-shell-anthropic-make-authentication :login t)))
```

ホストのそのプロジェクトで`M-x agent-shell-anthropic-start-claude-code`を実行してください。`:login t`はagent-shellにAPI keyを要求させないための設定です。コンテナへのログインは不要で、認証情報は通常の起動時にproxyへ注入したものを使います。

### Neovim Avante

[AvanteのACP provider](https://github.com/avante-corp/avante.nvim#acp-support)として登録します。既存の`require("avante").setup(...)`やlazy.nvimの`opts`へ、次の設定を追加してください。

```lua
require("avante").setup({
  provider = "claude-container",
  acp_providers = {
    ["claude-container"] = {
      command = "python3",
      args = { "/absolute/path/to/project/.devcontainer/claude/scripts/acp-agent.py" },
      env = {
        HOME = vim.env.HOME,
        DOCKER_HOST = vim.env.DOCKER_HOST,
        DOCKER_CONTEXT = vim.env.DOCKER_CONTEXT,
        DOCKER_CONFIG = vim.env.DOCKER_CONFIG,
      },
    },
  },
})
```

ホストのそのプロジェクトでAvanteを開きます。ほかのproviderを使っている場合は、`:AvanteSwitchProvider claude-container`で切り替えられます。このproviderに`auth_method`やAPI key、`ACP_PERMISSION_MODE`を設定する必要はありません。権限モードはコンテナ側のClaude設定を引き継ぎ、ACPのモード選択で変更できます。

`env`はホストのDocker CLIが接続先の設定を読むためのものです。Avanteは通常の環境変数をすべて引き継ぐわけではないので、Dockerの接続にほかの変数が必要な場合もここに追加してください。これらはコンテナへ転送されません。

### パスの変換

- `session/new`などでクライアントが渡すホストのプロジェクトパスを`/workspace`へ変換します。サブディレクトリも同じ相対位置へ変換します。ホストでシンボリックリンク経由のパスを開いた場合も、実体がこのプロジェクト内なら変換できます。ほかのリポジトリを作業場所に指定するとエラーになります。
- プロンプト中の構造化された`file://` URIも変換します。通常の文章や添付したテキストは書き換えません。文章でファイルの場所を伝えるときは、プロジェクト相対パスを使ってください。
- agentからの通知では、ツールの対象ファイル（`locations`）、差分のパス、`file://`のリソース、セッション一覧の`cwd`を、`/workspace/...`からホストのパスへ戻します。エディタは対象ファイルを開いたり、差分を表示したりできます。ツールのタイトルや本文、ツールの生の入出力（`rawInput`・`rawOutput`）は書き換えません。agentから受け取ったパスはホストで解決せず、文字列の前置きだけを置き換えます。
- `.git`は変換しません。sharedの`/workspace/.git`はagent専用のvolumeで、ホストの`.git`とは別物です。`.git`の中を作業場所に指定するとエラーになり、そこを指すURIはそのまま渡ります。`/workspace`の外（`/home/node/...`など）のパスも、コンテナのパスのまま表示します。

### 隔離の範囲

- agentの編集は共有の作業ツリーへ直接書かれ、保存済みのファイルとしてエディタに見えます。エディタの未保存のバッファはagentに渡りません。編集中のファイルをagentが書き換えると、エディタ側で再読み込みや競合の解消が必要になります。agentのコミットはこれまでどおり[ホストでレビューして取り込み](#ホストでレビューして取り込む)ます。sharedで共有される範囲は[隔離の範囲](network-policy.md#sharedで共有されるもの)も参照してください。
- クライアントのファイル読み書き（`fs`）・ターミナル・拡張機能はagentへ公開しません。作業ツリーは共有していても、エディタ経由の読み書きはホストのユーザーとして行われ、agentのread-onlyの`.devcontainer`、`shared_hidden_paths`、専用の`.git`を迂回し、agentが作ったsymlinkもホストで辿ります。agentは自分のmountの上で編集し、エディタはその結果をディスクで見ます。
- 中継はagentからの`session/update`通知と`session/request_permission`要求だけを通し、それ以外の要求を拒否します。双方向の`_meta`拡張も除きます。クライアント独自のSDK設定も転送しません。ツール実行とファイル編集はコンテナ内のClaude Codeが行います。
- 承認要求はエディタに表示されます。ログイン用のホストコマンドやブラウザ認証、クライアント拡張によるフォームなどは使えません。認証を更新するときは、既存の認証情報注入スクリプトを使ってください。
- エディタで明示的に添付した選択範囲・ファイル本文は、プロンプトとしてコンテナへ送られます。`shared_hidden_paths`で隠したファイルも、エディタで添付すれば中身が渡ります。中継はその内容をマスクしません。
- エディタが通知のパスを開くとき、そのファイルはホストで開かれます。agentは作業ツリーにsymlinkを置けるので、心当たりのないパスを開く前に中身を確認してください。
- クライアントが渡す`mcpServers`は常に空にします。エディタのMCP設定に含まれる認証値をコンテナへ渡さないためです。MCPサーバーはコンテナ内の`.mcp.json`などで設定し、認証値は既存のproxyで注入してください。
- ホストの環境変数や認証値を`docker exec`へ転送しません。アダプターはコンテナの`node`ユーザーで動き、通常のCLIと同じ`/usr/local/bin/claude`を使います。
- 中継の`acp-agent.py`は`.devcontainer`の下にあり、sharedではagentからread-onlyです。agentはホストが実行するこの境界を書き換えられません。

中継の標準出力はACPのJSON-RPC専用です。シェルのラッパーを加える場合も、挨拶などの文字列を標準出力へ出さないでください。`docker exec -it`や、アダプターへの直接接続でこの中継を置き換えると、ここで説明したホスト側の制限が適用されません。

## ホストのIMEでエディタを使う（隔離エディタ）

`[codeServer] enabled = true` で、agentの`/workspace`をホストの画面とIMEで編集できます。
agentの中でCoderのcode-serverを動かし、ホストでは専用のElectronシェルがそれを表示します。
シェルは、すべての通信を専用のproxyへ送り、agentのIDEとWebアプリ以外への通信、新規ウィンドウ、外部アプリケーションの起動を拒否します。
Linux（Wayland／X11）とWindows + WSLのどちらも、シェルをホストで直接動かします。
Chromiumのレンダラーsandboxは有効ですが、Electron全体を囲むOSの隔離はありません。
[VS Codeが作る経路](#vs-codeが作る経路)はどれもできません。
セットアップと制約は[隔離エディタの手順](../.devcontainer/claude/code-server/README.md)を参照してください。

## VS Codeで開く

agentが動いているコンテナに、VS Codeを接続しないでください。VS Codeで接続すると、VS Codeとその拡張機能が、コンテナからホストへ通じる経路をいくつか作ります。どの経路もproxyとfirewallを通りません。VS Codeのサーバーはagentと同じ`node`ユーザーで動くので、コンテナで動くプロセスはすべてこれらの経路を使えます。その中には、ホストでコマンドを実行する経路と、proxyを通らずに外部と通信する経路があり、どちらも遮断できません。

コンテナの中でエディタを使う場合は、[隔離エディタ](#ホストのimeでエディタを使う隔離エディタ)を参照してください。agentの変更を見るときは、[ホストでレビューして取り込む](#ホストでレビューして取り込む)の手順でホストのリポジトリへfetchし、ホストで差分を見てください。VS Codeを使う場合も、ホストで開いたリポジトリのまま差分を見てください。fetchしただけではファイルを取り出さないので、agentが書いた`.vscode/`の設定などをホストのVS Codeが実行することはありません。

### VS Codeが作る経路

| 経路 | コンテナで動くプロセスができること | 遮断 |
|---|---|---|
| リモートの拡張機能（`~/.vscode-server`） | ホスト側のターミナルを開かせて、ホストで任意のコマンドを実行する | 遮断できない |
| ホストのブラウザ（`$BROWSER`、`code --openExternal`） | ホストのブラウザでURLやWebページを開き、proxyを通らずに外部と通信する | 遮断できない |
| Dev Containers拡張機能のIPCソケット（`/tmp/vscode-remote-containers-ipc-*.sock`） | ホストで`git credential`を実行させて、任意の接続先のGitの認証情報を受け取る。保存と削除もできる。Dockerの認証情報も同じように扱える | 遮断できない |
| SSH agentの転送（`/tmp/vscode-ssh-auth-*.sock`） | ホストのSSH鍵で署名する。ブラウザの経路と組み合わせれば、SSHの認証を中継して、利用者としてpushできる | VS Codeを`SSH_AUTH_SOCK`なしで起動すれば遮断できる |
| VS CodeのGitHubアカウント（`/tmp/vscode-git-*.sock`） | VS CodeでサインインしているGitHubアカウントのトークンを受け取る。scopeには`repo`と`workflow`が含まれる | ホストの設定で遮断できる（`github.gitAuthentication`） |
| Waylandのソケット（`/tmp/vscode-wayland-*.sock`） | ホストの画面にウィンドウを出す。compositorによっては、画面の取得や入力の送信もできる | ホストの設定で遮断できる（`dev.containers.mountWaylandSocket`） |
| X11の転送（`/tmp/.X11-unix/X*`） | ホストの画面を取得し、キー入力とクリップボードを読む | 同梱のimageで遮断済み |
| gpg-agentの転送 | ホストのGPG鍵で署名する | 同梱のimageにはGnuPGがないので起きない |

この表は、Dev Containers拡張機能0.469.0とVS Code 1.119.0のコードから作りました。実際にコンテナから接続して確かめたのは、SSH agentとX11の転送、およびリモート拡張機能からのホスト側ターミナルでのコマンド実行です。ターミナル経由の実行は2026-10-14にLinuxホストで確認しました（[検証結果](testing.md#vs-codeのリモート拡張機能からホストでコマンドを実行)）。Windowsでは未検証です。

- リモートの拡張機能: `~/.vscode-server`の拡張機能は`node`の所有なので、コンテナのプロセスはそのコードを書き換えられます。書き換えたコードは、ウィンドウの再読み込みや再接続で拡張機能ホストが起動し直したときに読み込まれます。リモートの拡張機能は、`workbench.action.terminal.newLocal`でホスト側のターミナルを開き、`workbench.action.terminal.sendSequence`でそこへ文字列を送れます。
- ホストのブラウザ: VS Codeは、信頼済みのドメインでないURLを開く前に確認のダイアログを出します。ただし、`localhost`、`127.0.0.1`、`[::1]`のURLと、VS Codeが最初から信頼済みにしているドメインのURLは、確認なしに開きます。信頼済みのドメインには、`https://*.github.dev`のように、GitHub Codespacesの公開ポートとして第三者が用意できるものも含まれます。`localhost`のURLは、コンテナの同じポートをホストへ転送してから開くので、コンテナが配信したWebページがホストのブラウザで動きます。このページは、外部のサーバーとコンテナの間で通信を中継できます。
- IPCソケット: VS Codeは、VS Codeのプロセスの環境変数で`git credential`を実行し、ホームディレクトリの`~/.docker/config.json`とそのcredential helperを読みます。接続先のホストは、要求する側が指定します。
- X11: Dev Containers拡張機能はコンテナの環境変数に`DISPLAY`があるとX11を転送しないので、imageで`DISPLAY=none`を設定しています。VS Codeのサーバーとターミナル、`devcontainer exec`で起動したプロセスでは、`DISPLAY`を空にします。Playwrightのように、`DISPLAY`に値があれば画面付きでブラウザを起動するツールがあるためです。

### それでもVS Codeで接続する場合

接続している間にコンテナで動くプロセスは、agentでなくても上の経路を使えます。人間が実行するテストやビルドに加え、VS Codeの拡張機能が実行するworkspaceのコード（ESLintやTypeScriptの設定、Gradleのビルドスクリプトなど）も含みます。そのため、VS Codeでの接続は、ホストでagentのコードを実行するのと同じ扱いになります。次の手順に従ってください。

1. ホストのVS Codeのuser設定に、次を書いてください。遮断できる経路を遮断し、コンテナのGitとDockerがホストの認証情報を自動で使わないようにする設定です。

   ```json
   {
     "dev.containers.copyGitConfig": false,
     "dev.containers.gitCredentialHelperConfigLocation": "none",
     "dev.containers.dockerCredentialHelper": false,
     "dev.containers.githubCLILoginWithToken": false,
     "dev.containers.mountWaylandSocket": false,
     "github.gitAuthentication": false
   }
   ```

   - `copyGitConfig`は、ホストの`~/.gitconfig`をコンテナへコピーしない設定です。
   - `gitCredentialHelperConfigLocation`と`dockerCredentialHelper`は、IPCソケットを使うcredential helperを、コンテナのGitとDockerに登録しない設定です。IPCソケットは残るので、コンテナのプロセスが直接要求すれば認証情報を受け取れます。
   - `githubCLILoginWithToken`は、ホストの`gh`のトークンでコンテナの`gh`にログインしない設定です。
   - `mountWaylandSocket`は、Waylandのソケットをコンテナへmountしない設定です。
   - `github.gitAuthentication`は、VS CodeのGitHubアカウントのトークンをGitの認証に使わない設定です。user設定なので、ホストで開いたリポジトリにも適用されます。

2. agentに作業をコミットさせ、ホストでfetchしてレビューしてください。未コミットの変更が残っていると、レビューしていないコードをVS Codeの拡張機能が実行します。
3. agentを止め、ホストでコンテナを作り直してください。agentが残したプロセスや、書き換えた`~/.vscode-server`を消すためです。

   ```sh
   devcontainer up --remove-existing-container
   ```

4. ホストのVS CodeにDev Containers拡張機能を入れ、リポジトリで`code .`を実行し、「Dev Containers: Reopen in Container」を実行してください。作業ディレクトリは`/workspace`になります。コマンドパレットの「Dev Containers: Install devcontainer CLI」で入れたCLIなら、`devcontainer open .`でも開けます。npmやGitHubのリリースから入れたdevcontainer CLIには、`open`がありません。
5. 接続している間は、agentを動かさないでください。ウィンドウを閉じるとComposeも停止します。

[READMEに戻る](../README.md)
