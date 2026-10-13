## 評価 by Fable

`devcontainer exec claude` で使う限り、今の構成は必要水準をすべて満たし、系統4（nas）相当です。ただし VS Code を接続すると、Dev Containers 拡張がコンテナに持ち込む橋渡しが proxy と firewall の外側に生じ、A1a・A2・B2a が崩れます。提案の 4 設定は正しいのですが、それだけでは足りません。SSH エージェント転送、X11 転送、ホストブラウザ起動が残ります。

### README の評価軸への当てはめ

| 要求 | 必要水準 | CLI 起動時 | VS Code 接続時（現状） | 根拠 |
| :-- | --: | --: | --: | :-- |
| A1a 未許可送信先への流出 | ◎ | ◎ | ○ | 実測で DNS 失敗、proxy 迂回の TCP 失敗、許可外ホストは 403。しかし `$BROWSER` と `code --openExternal` がホストのブラウザで任意 URL を開く |
| A1b-1 利用者の認証情報 | ◎ | ◎ | ◎ | read-only PAT に加え、proxy が agent の Authorization を捨てて置き換える。push 経路は [claude.toml:40](.devcontainer/claude.toml#L40) で外している |
| A1b-2 持ち込まれた認証情報 | ○ | ◎ | ◎ | 全ホストで proxy が認証ヘッダを上書きする。Anthropic 向けは本文も検査する |
| B1 本番変更 | ◎ | ◎ | ◎（条件付き） | 到達先が proxy の許可リストに限られ、DinD は無通信。ただし git/docker の credential helper でホストに保存した ci1 等の認証情報を取り出せるなら、「持たせない」という前提が崩れる |
| B2a ホストの破壊・改変 | ◎ | ◎ | × | 独立 clone、managed settings は root 所有、agent は capability なし・seccomp 有効。しかし X11 ソケットが認証なしで接続でき、ホスト画面の取得やキー入力の監視ができる |
| A2 / A3 | ○ | ○ | ○未満 | sumi の broker は agent の外にあり、GH_TOKEN 等はダミー。VS Code 経由ではホストの git/docker 認証情報と SSH 鍵の署名が取れる |
| B2b | ○ | ○以上 | ○以上 | ホスト側の worktree には触れない |
| P1 未信頼情報源 | ○ | ○ | ○ | 実測で GraphQL から `octocat/Hello-World` を読めた。npm registry と Redmine も GET が全面許可 |

### VS Code 側で必要な設定

提案の 4 設定は、コンテナ内で次の証拠と対応します。

- `gitCredentialHelperConfigLocation: none` は `/etc/gitconfig` に書かれた VS Code の credential helper を消します。agent からの `git credential fill` でホストの認証情報が返る経路です。
- `dockerCredentialHelper: false` は `~/.docker/config.json` の credsStore と `/usr/local/bin/docker-credential-dev-containers-*` を消します。
- `githubCLILoginWithToken: false` は今回の環境では痕跡がありませんでしたが、設定しておくべきです。
- `copyGitConfig: false` は、git 設定の持ち込みを `extra` の経路だけにします。

4 設定を入れても残る橋渡しが 3 つあります。

- SSH エージェントの転送です。`/tmp/vscode-ssh-auth-*.sock` に接続できました。通信は塞がれていますが、ホストの SSH 鍵で任意データに署名できるので、`gpg.format=ssh` にすれば利用者の署名付きコミットを作れます。
- X11 の転送です。`/tmp/.X11-unix/X2` に接続し、認証なしで setup が Success を返しました。画面の取得、クリップボードの読み取り、キー入力の監視がホストに対してできます。
- ホストブラウザの起動です。`$BROWSER` と `code --openExternal` が URL をホストで開きます。VS Code の trusted domains の確認ダイアログが出るはずですが、`workbench.trustedDomains` にワイルドカードがないことを確認してください。

この 3 つを止める拡張の設定は見つけられませんでした。選択肢は、agent のコンテナには VS Code を接続せず CLI で使うか、ホスト側で `SSH_AUTH_SOCK` を外して VS Code を起動し X11 とブラウザは受け入れるか、のどちらかになります。

### claude-devcontainer 側の改善点

- [git-agent.py:38](.devcontainer/claude/scripts/git-agent.py#L38) が `protocol.ext.allow=always` を設定しています。`always` は submodule の初期化にも及ぶので、agent の変更をマージした後の `.gitmodules` に `ext::` の URL があると `git submodule update --init` でホスト上でコマンドが実行されます。`user` にすれば `git fetch agent` は動き、submodule 経由は拒否されます。
- [README.md:111](.devcontainer/README.md#L111) の受け取り手順に、マージ後にホストで実行されるファイルの一覧を書くべきです。`.devcontainer/` 配下全部（`claude.toml` の `proxy.hosts` と `docker.images`、`prepare-deps.sh`、`claude/scripts/`）、`.gitmodules`、`flake.nix`、`.vscode/`、`.envrc` が対象です。独立 clone の設計により B2a はレビューで止める構造になっているので、何を見るかを明示しないと意味がありません。
- [compose.yaml:149](.devcontainer/claude/compose.yaml#L149) の DinD が privileged です。rootless dockerd で緩和していますが、カーネルの脆弱性があればホスト root に至ります。agent 本体は seccomp 有効・capability なしで、系統3 の unconfined より良い状態です。

未検証の点が 2 つあります。credential helper が実際にホストの認証情報を返すかの確認コマンドは、auto mode の classifier に拒否されたため実行していません。`openExternal` の確認ダイアログの有無も、ホストのブラウザを開く操作になるので試していません。

この評価を `.devcontainer/README.md` への追記や別文書にまとめる場合は言ってください。

## その他

* VS Code extのフォルダもvolumeにしたい。Reopenで数十秒待たされる
