# 接続できない場合

## 初回cloneが失敗する

`Could not import the local Git snapshot`なら、ホストの`.devcontainer/.generated/`に初期化用Gitコピーが存在し、コンテナのnodeユーザーから読めるか確認してください。初回cloneにネットワーク接続やpushは不要です。コピーを再生成して再試行できます。

```sh
python3 .devcontainer/claude/scripts/workspace-source.py
.devcontainer/claude/scripts/compose.sh exec --user node agent wait-ready.sh
```

初期化途中は`.git/claude-bootstrap.json`に記録したコミット・ブランチ名・コピー名を使い続けます。開始時のコピーは再試行のため保持するので、完了するまでは`.generated/workspace-*.git`を削除しないでください。ホストのHEADが進んでも、再試行は元のコミットを使います。既存ファイルや依存マウントとの衝突が報告されたら、volumeの内容を確認してください。未完了のリポジトリを自動削除する処理はありません。

## git fetch agentが失敗する

コンテナが起動していることを確認してください。`Expected exactly one running agent container`なら、このホスト作業ディレクトリに対応するagentが起動していないか、複数存在します。再作成後は自動で新しいコンテナへ接続するので、IPやURLの設定変更は不要です。

`Remote agent already points elsewhere`なら、既存の`agent`を別名にしてから起動してください。起動処理は別用途のリモートを上書きしません。`origin`の変更は不要です。

## HTTP 403が返る

proxyが要求を拒否しています。本文が`blocked: no credentials`の場合は、proxyに認証情報がありません。ホストで`.devcontainer/claude/scripts/inject-credentials.py`を実行して渡してください。`blocked: no secret GITHUB_TOKEN`のように秘密の名前が出る場合は、`claude.toml`の`proxy.hosts`が参照する秘密が、ホストの`~/.config/claude-devcontainer/credentials`で代入されていません。代入してから同じスクリプトを実行してください。起動時の自動注入の記録は`~/.local/state/claude-devcontainer/inject.log`にあります。

認証値を渡した後も403が返る場合は、proxyのログで拒否された要求を確認してください。

ホストで取り込んだリポジトリのルートで次のコマンドを実行してください。

```sh
.devcontainer/claude/scripts/compose.sh logs proxy | tail
```

`request-denied POST api.anthropic.com/v1/messages (tool type web_search_20250305)`のように、拒否した要求のメソッド、ホスト、パスと、拒否した理由が出ます。理由の`route`は許可していないパス、`host`は許可していない接続先、`tool type …`や`field …`はAnthropicの要求本文の検査で拒否したことを示します。proxyはクエリをログに出しません。Claude Codeが設定やテレメトリの取得に使う`/api/...`の拒否は通常の動作で、Claudeはそれらなしで動きます。それ以外の拒否は、[許可される通信](network-policy.md#許可される通信)と照らし合わせてください。

## 接続がタイムアウトする

agentはproxyとDinDにしか接続できません。`HTTPS_PROXY`を経由しないプログラムの通信はタイムアウトします。proxyが停止している場合も同様です。`docker ps`でproxyが起動していることを確認してください。

[READMEに戻る](../README.md)
