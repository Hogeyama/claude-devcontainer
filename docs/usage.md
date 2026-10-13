# 追加の利用手順

基本の起動・終了手順は[README](../README.md)を参照してください。この文書のホスト向けコマンドは、取り込んだリポジトリのルートで実行してください。認証情報は、ホストのClaude Codeのログインと`~/.config/claude-devcontainer/credentials`から読みます。

## 初回起動

VS Code の「Reopen in Container」か `devcontainer up` で起動する。ホストのローカルHEADとその履歴をコピーし、空のworkspace volumeにホストと同じブランチ名でcheckoutする。ホストがdetached HEADならコンテナ側もdetached HEADにする。ブランチ名を合わせるのは初回作成時だけで、以降はコンテナ側のブランチを維持する。事前のpushやコンテナ内での手動cloneは不要である。未pushのコミットも使えるが、未コミット・未追跡ファイルはコピーしない。

## 受け渡し用リモート

起動処理がホストに`agent`リモートを自動登録する。接続先は起動中のコンテナ内の`/workspace`であり、GitHubなどの外部リポジトリは介在しない。既存の`origin`は変更しない。同名の`agent`が別用途で存在する場合は上書きせず停止するので、既存のリモートを別名にしてから起動する。

Git標準の`ext`転送とホスト側の`git-agent.py`を使い、Dockerの標準入出力をコンテナ内の`git-upload-pack` / `git-receive-pack`につなぐ。どちらも`node`で実行する。SSHサーバーや公開ポートは不要で、接続時にGitを実行したworktreeとそのコンテナを見つけ直すため、複数worktreeや再作成にも追従する。このホストリポジトリだけに`protocol.ext.allow=always`を設定する。接続にはコンテナが起動している必要がある。

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

## ホストの変更を渡す

ホストから、コンテナの受け取り用ブランチへpushする。

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

`devcontainer up`で起動すると、注入スクリプトは環境が動いている間ホストで動き続けます。proxyが作り直されれば、その都度注入し直します。

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

## VS Codeで開く

VS CodeのGUI接続と、ホスト認証が転送されないことは未検証です。Dev Containersには[ホストのGit認証を共有する機能](https://code.visualstudio.com/remote/advancedcontainers/sharing-git-credentials)があります。使う場合は認証情報のない専用ホストアカウントと専用editor profileを用意してください。SSH／GPG agentや、ホストのGitHub／Docker認証を用意・転送しないことが利用条件です。

認証用環境変数を渡さずに、専用profileのVS Codeを起動してこのリポジトリを開いてください。Dev Containersの「Reopen in Container」を実行すると、作業ディレクトリは`/workspace`になります。ホストのホームや認証用socketを追加mountしないでください。起動前にホストのcredentialsファイルを設定してください。自動注入と初回cloneが終わるまで起動処理が待ちます。その後はVS Codeのコンテナ内ターミナルで通常どおり`claude`を実行できます。

ビルド、テスト、デバッグやコードを実行する拡張機能はコンテナ内で動かしてください。改変されたGradle設定を人間が実行する場合も、コンテナ内であれば通信制限の対象です。ホストで実行するスクリプト、Dockerfile、起動設定は先にレビューしてください。

### 認証転送を抑制する設定

Dev Containers拡張機能0.469.0では、以下をホストのuser/profile設定に指定できます。

```json
{
  "dev.containers.copyGitConfig": false,
  "dev.containers.gitCredentialHelperConfigLocation": "none",
  "dev.containers.dockerCredentialHelper": false,
  "dev.containers.githubCLILoginWithToken": false
}
```

これらは認証helperの設置などを抑制する設定です。既存のsocketや拡張機能を通じた認証転送まで遮断できたという検証結果はありません。`remoteEnv`で環境変数を消すだけでも不十分です。

[READMEに戻る](../README.md)
