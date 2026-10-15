# 追加の利用手順

基本の起動・終了手順は[README](../README.md)を参照してください。この文書のホスト向けコマンドは、取り込んだリポジトリのルートで実行してください。認証情報は、ホストのClaude Codeのログインと`~/.config/claude-devcontainer/credentials`から読みます。

## Gitの共有と分離

モードは`clone`と`shared`の2種類。旧`worktree`設定は`shared`に変更する。従来のshared（ホストの`.git`を直接read-onlyで参照する方式）は廃止した。更新後はコンテナを再作成する。

`.devcontainer/claude.toml`、またはホストだけの`.devcontainer/claude.local.toml`で指定する。省略時は従来どおり`clone`。ローカル設定が優先される。

```toml
[git]
isolation = "clone" # "shared" にするとホストの作業ツリーを共有
```

| 設定 | 作業場所 | Gitと変更の受け渡し |
| --- | --- | --- |
| `clone` | 独立した永続volume。初回のみホストHEADをコピー | 独立した`.git`。ホストから`git fetch/push agent` |
| `shared` | ホストの作業ツリーを`/workspace`へread-write mount | `/workspace/.git`はagent専用のvolume。ホストの`.git`は`/run/host/git`にread-only。ホストから`git fetch agent`と`git reset --mixed` |

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

## 初回起動（clone、shared）

`devcontainer up` で起動する。ホストのローカルHEADとその履歴をコピーし、cloneでは空のworkspace volumeにホストと同じブランチ名でcheckoutする。sharedでは同じコピーから`agent-git` volumeにHEADとindexだけを作り、ホストの作業ツリーには書かない。ホストがdetached HEADならコンテナ側もdetached HEADにする。ブランチ名を合わせるのは初回作成時だけで、以降はコンテナ側のブランチを維持する。事前のpushやコンテナ内での手動cloneは不要である。未pushのコミットも使えるが、未コミット・未追跡ファイルはコピーしない。

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

cloneでは、ホストからコンテナの受け取り用ブランチへpushする。

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

ルートの`node_modules`はホストをlower、agent専用の永続ディレクトリをupper/workとするCoWです。ホストに存在しなければ空のディレクトリを作ります。agentの書き込み・削除はupperに残り、ホストやpnpmのstoreには書き戻しません。upper/workの場所は`.devcontainer/.generated/settings.env`の`CLAUDE_DEVCONTAINER_NODE_UPPER`と`CLAUDE_DEVCONTAINER_NODE_WORK`で確認できます。既定は`$XDG_STATE_HOME`（未指定なら`~/.local/state`）以下で、リポジトリの絶対パスごとに分かれます。overlayのオプションとして表現できない空白、カンマ、コロン、バックスラッシュ、引用符を含むパスは拒否します。

npm、通常のpnpm、Yarnのnode_modulesモードを対象にします。Yarn PnPや外部のvirtual storeは自動対応しません。native addonにはホストとコンテナのNode・libc等の互換性が必要です。新しい依存をオンライン取得する仕組みではありません。

モノレポの各パッケージにも依存が必要なら、ホストで対応するlower/upper/workを用意して、`.devcontainer/compose.yaml`へ同じ方式のvolumeを追加します。以下の絶対パスは自分の環境のものへ置き換えてください。upper/workは同じファイルシステム上、リポジトリとビルド対象の外に置き、agentのUIDで書けるようにします。

```yaml
services:
  agent:
    volumes:
      - type: volume
        source: app-node-modules
        target: /workspace/packages/app/node_modules
        volume: {nocopy: true}
volumes:
  app-node-modules:
    driver_opts:
      type: overlay
      device: overlay
      o: lowerdir=/absolute/repo/packages/app/node_modules,upperdir=/absolute/state/app/upper,workdir=/absolute/state/app/work
```

初回cloneは実際の`node_modules`マウントとその祖先ディレクトリだけを許容します。普通の既存ファイルや、取得コミットの追跡パスとマウントの衝突は拒否します。

CoWはスナップショットではないため、mount中にホストのlowerを変更しないでください。依存を更新するときは次の順です。

1. agent側の依存への変更に残すものがあれば退避する。
2. ホストで`.devcontainer/claude/scripts/compose.sh down`を実行し、コンテナを停止・削除してoverlayをアンマウントする。`--volumes`は付けない。
3. ホストで使っているpackage managerにより依存を更新する。
4. `settings.env`に記載されたNode用のupper/workディレクトリだけを削除し、同じ場所に空で作り直す。overlayfsが作るwork内のroot所有の領域の削除には、管理者権限が必要な場合がある。追加したパッケージ用overlayも同様に更新する。
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

Claudeの認証には、ホストのClaude Codeのログインを使います。ホストで`claude`にログインしていれば、設定は要りません。ログインし直した場合や、`~/.config/claude-devcontainer/credentials`で`proxy.hosts`が参照する秘密（`GITHUB_TOKEN`など）を変えた場合は、注入スクリプトを再実行してください。proxyやagentの再起動・再ビルドは不要です。

```sh
python3 .devcontainer/claude/scripts/inject-credentials.py
```

proxyは1環境につき1組の認証値を持ち、次の要求から新しい値を使います。認証値はproxy専用tmpfsに保存され、置き換えるかproxyを停止するまで有効です。ファイルはproxyのUID 1000（agentのUIDの設定とは無関係）が所有する0400で、agentやDinDからは読めません。

`devcontainer up`で起動すると、注入スクリプトは環境が動いている間ホストで動き続けます。proxyやagentが作り直されれば、その都度注入し直します。起動から10分間は、環境が消えても終了しません。`devcontainer up --remove-existing-container`はagentを消してからイメージをビルドするので、その間も待ち続けて新しいagentに注入します。

### ホストのClaude Codeのログイン

注入スクリプトは、ホストのClaude Codeのログイン情報（`$CLAUDE_CONFIG_DIR`、なければ`~/.claude`の`.credentials.json`）からaccess tokenを読み、proxyへ渡します。別の場所のファイルを使う場合は、`~/.config/claude-devcontainer/credentials`に`CLAUDE_CREDENTIALS_FILE=<パス>`と書いてください。

- agentには本物のトークンを見せません。
- 期限の5分前になると、ホストからAnthropicへ直接、トークンの更新を要求し、新しいトークンをファイルへ書き戻してからproxyへ渡し直します。ホストのClaude Codeと同じロックを取ってからファイルを読み直すので、ホストのClaude Codeと同時に更新して壊すことはありません。ホストのClaude Codeが先に更新した場合は、その値を使います。
- 書き戻すのは`claudeAiOauth`のaccess token、refresh token、期限だけです。
- 更新に失敗した場合は、今のトークンを使い続け、30秒後にやり直します。ホストで`claude /login`し直せば、新しいログインが使われます。
- agentのClaude Codeには、ログインのscope、subscriptionの種類などを`/etc/claude-code/managed-settings.d/60-claude-login.json`で渡します。トークンは含みません。
- 更新の要求の形式は、Claude Code 2.1.295の実装に合わせています。Claude Codeがこれを変えると、更新は失敗します。その場合も、ホストのClaude Codeを使っていれば、その更新した値を読み直して動き続けます。

`claude setup-token`のトークン（`CLAUDE_CODE_OAUTH_TOKEN`）は使いません。推論用のscopeしか持たず、claude.aiのコネクタが使えないうえ、期限切れを自動で更新できないためです。設定ファイルに残っていると、注入スクリプトが警告を出して無視します。

### API keyを使う

ログインの代わりに、Claude ConsoleのAPI keyも使えます。`~/.config/claude-devcontainer/credentials`に`ANTHROPIC_API_KEY=…`と書いてください。API keyでは、Claude CodeがArtifactsとclaude.aiのコネクタを使えません。API keyは自動では更新しないので、Consoleで更新したら注入スクリプトを再実行してください。

注入コマンドは起動済みの環境だけを対象にします。環境がない場合や対象が複数ある場合は失敗するので、先に`devcontainer up`を実行してください。`proxy.hosts`が参照する秘密は、`~/.config/claude-devcontainer/credentials`で代入したものだけを渡し、環境変数からは取りません。Claudeの認証（`ANTHROPIC_API_KEY`か`CLAUDE_CREDENTIALS_FILE`）は環境変数でも指定でき、その場合はファイルの指定より優先します。

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

agentにツールを追加する場合は、[`.devcontainer/agent-setup.sh`](../.devcontainer/agent-setup.sh)に導入手順を書いてください。ホストでagentのimageをビルドする際にrootで実行されます。

コンテナのTCPポートは、`docker run -p 8080`または`-p 18080:8080`のように、既定の`0.0.0.0`で公開してください。agentからは、`127.0.0.1:公開ポート`に接続できます。loopbackだけで公開したポートは中継されません。Docker API用の2375番ポートも公開先としては使えません。

Testcontainersでは、テスト対象とRyukのイメージを事前に投入してください。以下の接続設定はComposeで設定済みです。

```sh
DOCKER_HOST=tcp://127.0.0.1:2375
TESTCONTAINERS_HOST_OVERRIDE=127.0.0.1
TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/run/user/1000/docker.sock  # 1000はagentのUID
```

## ホストのIMEでエディタを使う（隔離エディタ）

`[codeServer] enabled = true` で、agentの`/workspace`をホストの画面とIMEで編集できます。
agentの中でCoderのcode-serverを動かし、ホストでは専用のElectronシェルがそれを表示します。
シェルは、すべての通信を専用のproxyへ送り、agentのIDEとWebアプリ以外への通信、新規ウィンドウ、外部アプリケーションの起動を拒否します。
Linux（Wayland）では、シェルをさらにbubblewrapに入れ、ループバックしかないnetwork namespaceで動かします。
Windows + WSLでは管理者権限を使わないので、この外側の壁はありません。
[VS Codeが作る経路](#vs-codeが作る経路)はどれもできません。
todeとは独立した機能です。セットアップと制約は[隔離エディタの手順](../.devcontainer/claude/code-server/README.md)を参照してください。

## ターミナルでVS Codeを使う（tode）

agentのコンテナでVS Codeを使いたい場合は、VS Codeを接続する代わりに、[terminal-code](https://github.com/zenbu-labs/terminal-code)（`tode`）をコンテナの中で動かしてください。`tode`は、code-serverと、それを表示するChromium（Electron）をまとめて動かし、描いた画面をkitty graphics protocolでターミナルへ送ります。

`claude.toml`で有効にすると、agentのimageに入ります。変更後は`devcontainer up --remove-existing-container`で作り直してください。

```toml
[tode]
enabled = true
```

kitty graphics protocolに対応したターミナル（kitty、Ghosttyなど）から、次のように起動します。

```sh
devcontainer exec tode /workspace
```

### 拡張機能を入れる

拡張機能は、`tode.extensions`に並べるとimageのビルド時にVS Code Marketplaceから入ります。agentはMarketplaceへ接続できないので、コンテナの中からはインストールできません。バージョンを固定するときは`@`で付けます。変更後は`devcontainer up --remove-existing-container`で作り直してください。

```toml
[tode]
enabled = true
extensions = ["ms-python.python", "golang.go@0.48.0"]
```

一覧にない拡張機能を試すときは、ホストでダウンロードした`.vsix`をコンテナへ置き、`tode --install-extension <vsixのパス>`で入れてください。この方法で入れた拡張機能は、コンテナを作り直すと消えます。

### 設定の保存

VS Codeのユーザー設定（`settings.json`）、キー割り当て、snippets、拡張機能が保存する状態は、`tode-user` volumeに置くので、コンテナを作り直しても残ります。拡張機能とMachine設定はimageに入れたものを使うので、`tode.extensions`やimageの変更は作り直すと反映されます。コンテナの中で`tode --install-extension`で入れた拡張機能は残りません。

### 隔離の範囲

code-server、Chromium、拡張機能はすべてコンテナの中で動きます。ホストとの接点は`devcontainer exec`の標準入出力だけで、ホストへはターミナルの制御シーケンスと画像が、コンテナへはキー入力とマウス操作が流れます。そのため、[VS Codeが作る経路](#vs-codeが作る経路)はどれもできません。拡張機能やChromiumの通信はproxyとfirewallを通り、URLを開く操作もコンテナの中のChromiumで開きます。拡張機能やワークスペースのコードは、agentと同じ`node`ユーザーとして同じコンテナで動くので、agentにできないことはできません。

残る経路はターミナルです。コンテナで動くプロセスは、`tode`でなくても、ターミナルへ任意の制御シーケンスを送れます。ホストのターミナルで次を確認してください。

- クリップボードの読み取り（OSC 52、kittyのclipboard protocol）を、拒否するか確認付きにする。kittyは`clipboard_control`、Ghosttyは`clipboard-read`で設定でき、どちらも既定は確認付きです。`tode`の画面は、ターミナル経由でクリップボードを読むことがあります。
- kitty graphics protocolのうち、ファイルや共有メモリを名前で渡す転送（`t=f`、`t=t`、`t=s`）では、ターミナルがホストのファイルや共有メモリを読みます。読んだ内容はコンテナへ返りませんが、ターミナルの応答から存在の有無は分かります。`tode`はこれらを使わず画像を直接送るよう、imageで`TERMINAL_BROWSER_FRAMES=inline`を設定しています。
- ターミナルは最新の版を使ってください。

### imageでの設定

- Chromiumのsandboxは、user namespaceかsetuidのhelperを必要とします。agentではseccompと`no-new-privileges`がどちらも拒否するので、`--no-sandbox`で起動します。rendererから抜け出したページは、コンテナの中の`node`ユーザーのプロセスになりますが、これはagent自身と同じ権限です。
- Chromiumはheadless Ozoneで起動し、`DISPLAY`と`WAYLAND_DISPLAY`を外します。転送されたディスプレイがあっても使いません。
- Chromiumは`127.0.0.1`でremote debugging portを開き、code-serverも`127.0.0.1`で待ち受けます。コンテナの中のプロセスは、これらを通じて画面を操作できます。agentはもともと同じユーザーなので、権限は増えません。
- 拡張機能のMarketplaceは、proxyで許可していないので、コンテナの中からは使えません。[拡張機能を入れる](#拡張機能を入れる)を参照してください。
- todeとcode-serverのtarballは、バージョンとSHA-256を固定してimageのビルド時にダウンロードします。todeは初回の起動時にcode-serverをGitHubから取りに行きますが、proxyが拒否するので、imageに入れたcode-serverを`TODE_CODE_SERVER`で指定しています。todeを更新するときは`agent/Dockerfile`のURLとchecksumを変え、code-serverも新しいtodeが固定するバージョン（`dist/codeserver/vendored.js`）に合わせてください。`tode --upgrade`は使えません。
- 日本語と絵文字を表示できるよう、Notoのフォントを入れています。
- Chromiumは`--disable-dev-shm-usage`で起動し、描画のバッファを`/dev/shm`でなく`/tmp`に置きます。Dockerの`/dev/shm`は64MBで、大きな画面では足りず、rendererがログを残さずに落ちて画面が固まるためです。
- code-serverのターミナルは`/bin/bash`で起動します。nixのdev shellはreadlineなしのBashをPATHの先頭に置き、このBashはプロンプトの`\[ \]`をそのまま表示するためです。dev shellの環境は`~/.bashrc`から読むので、ターミナルでも使えます。

## VS Codeで開く

agentが動いているコンテナに、VS Codeを接続しないでください。VS Codeで接続すると、VS Codeとその拡張機能が、コンテナからホストへ通じる経路をいくつか作ります。どの経路もproxyとfirewallを通りません。VS Codeのサーバーはagentと同じ`node`ユーザーで動くので、コンテナで動くプロセスはすべてこれらの経路を使えます。その中には、ホストでコマンドを実行する経路と、proxyを通らずに外部と通信する経路があり、どちらも遮断できません。

コンテナの中でエディタを使う場合は、[ターミナルでVS Codeを使う（tode）](#ターミナルでvs-codeを使うtode)を、ホストの画面とIMEで使う場合は[隔離エディタ](#ホストのimeでエディタを使う隔離エディタ)を参照してください。agentの変更を見るときは、[ホストでレビューして取り込む](#ホストでレビューして取り込む)の手順でホストのリポジトリへfetchし、ホストで差分を見てください。VS Codeを使う場合も、ホストで開いたリポジトリのまま差分を見てください。fetchしただけではファイルを取り出さないので、agentが書いた`.vscode/`の設定などをホストのVS Codeが実行することはありません。

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
