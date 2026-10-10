# 接続できない場合

## HTTP 403が返る

proxyが要求を拒否しています。本文が`blocked: no credentials`の場合は、proxyに認証情報がありません。ホストで`.devcontainer/claude/scripts/inject-credentials.py`を実行して渡してください。起動時の自動注入の記録は`~/.local/state/claude-devcontainer/inject.log`にあります。

認証値を渡した後も403が返る場合は、proxyのログで拒否された要求を確認します。

ホストで取り込んだリポジトリのルートで実行します。

```sh
project=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' \
  "$(docker ps -q --filter "label=devcontainer.local_folder=$(pwd -P)" --filter "label=com.docker.compose.service=agent")")
docker compose -p "$project" -f .devcontainer/compose.yaml logs proxy | tail
```

`request-denied POST api.anthropic.com/v1/messages`のように、拒否した要求のメソッド、ホスト、パスが出ます。クエリは出しません。Claude Codeが設定やテレメトリの取得に使う`/api/...`の拒否は通常の動作で、Claudeはそれらなしで動きます。それ以外の拒否は、[許可される通信](network-policy.md#許可される通信)と照らし合わせてください。

## 接続がタイムアウトする

agentはproxyとDinDにしか接続できません。`HTTPS_PROXY`を経由しないプログラムの通信はタイムアウトします。proxyが停止している場合も同様です。`docker ps`でproxyが起動していることを確認してください。

[READMEに戻る](../README.md)
