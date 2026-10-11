# 接続できない場合

## HTTP 403が返る

proxyが要求を拒否しています。本文が`blocked: no credentials`の場合は、proxyに認証情報がありません。ホストで`.devcontainer/claude/scripts/inject-credentials.py`を実行して渡してください。`blocked: no secret GITHUB_TOKEN`のように秘密の名前が出る場合は、`proxy.yaml`が参照する秘密が、ホストの`~/.config/claude-devcontainer/credentials`で代入されていません。代入してから同じスクリプトを実行してください。起動時の自動注入の記録は`~/.local/state/claude-devcontainer/inject.log`にあります。

認証値を渡した後も403が返る場合は、proxyのログで拒否された要求を確認してください。

ホストで取り込んだリポジトリのルートで次のコマンドを実行してください。

```sh
project=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' \
  "$(docker ps -q --filter "label=devcontainer.local_folder=$(pwd -P)" --filter "label=com.docker.compose.service=agent")")
docker compose -p "$project" -f .devcontainer/compose.yaml logs proxy | tail
```

`request-denied POST api.anthropic.com/v1/messages (tool type web_search_20250305)`のように、拒否した要求のメソッド、ホスト、パスと、拒否した理由が出ます。理由の`route`は許可していないパス、`host`は許可していない接続先、`tool type …`や`field …`はAnthropicの要求本文の検査で拒否したことを示します。proxyはクエリをログに出しません。Claude Codeが設定やテレメトリの取得に使う`/api/...`の拒否は通常の動作で、Claudeはそれらなしで動きます。それ以外の拒否は、[許可される通信](network-policy.md#許可される通信)と照らし合わせてください。

## 接続がタイムアウトする

agentはproxyとDinDにしか接続できません。`HTTPS_PROXY`を経由しないプログラムの通信はタイムアウトします。proxyが停止している場合も同様です。`docker ps`でproxyが起動していることを確認してください。

[READMEに戻る](../README.md)
