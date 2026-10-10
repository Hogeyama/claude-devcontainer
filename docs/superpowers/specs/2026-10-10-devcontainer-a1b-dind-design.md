# A1b の送信先制限と DinD を備える Dev Container

## 目的と確定した要件

このリポジトリに Claude Code 用 Dev Container を作る。`../nix-agent-sandbox/docs/architecture/threat-model.md` の系統3を基に、A1b（許可サービス内の未許可相手への送信）の ◎ を目標にする。Docker API とコンテナの公開 TCP ポートは、同リポジトリの `contrib/dind-bridge` で Claude Code の Bash sandbox 内から利用する。

ユーザー指定により、P1（未信頼情報源の取込み制限）は対象外とする。GraphQL パーサー、汎用ポリシー設定、承認 broker は作らない。通信ルールは `proxy/addon.py` に直接実装する。

GitHub には、共有を許可した非公開 repo だけを選択した fine-grained PAT を proxy が強制注入する。GraphQL 本文は検査せず、repo へのアクセス制限を GitHub の認可に担わせる。この方針はユーザー確認済み。

## 構成

Docker Compose で次の3サービスを起動する。

| サービス | 役割 | 接続するネットワーク |
| --- | --- | --- |
| agent | Claude Code、開発ツール、dind-bridge serve | proxy 用 internal network、DinD 用 internal network |
| proxy | mitmproxy、TLS 終端、要求検査、認証注入 | proxy 用 internal network、外向き network |
| dind | rootless dockerd、内側のコンテナ | DinD 用 internal network のみ |

agent と dind に外向き network を接続しない。agent と dind の network namespace は共有しない。ホストの Docker socket はどのサービスにも渡さない。proxy のポートをホストへ公開しない。

internal network だけでホスト宛て通信や DNS の迂回も防げると仮定しない。agent の起動処理で outbound を既定拒否にし、loopback、proxy の固定アドレス・port、dind の固定アドレスへの必要な TCP 通信だけを許可する。DNS は Docker の組込み resolver 宛ても含めて遮断する。サービス名は固定 hosts で解決する。初期化後は非 root で実行し、firewall を変更できる capability と sudo を渡さない。IPv6 も同じ境界で拒否する。

dind はエージェントが Docker API を通じて操作できる環境として扱う。dind 内の firewall だけを外向き通信の防御にしない。内部コンテナ、host network mode、rootless daemon の namespace から、外部・ホスト・外部 DNS へ到達しないことを実機で検証する。成立しない構成はサポート済みとして出荷しない。

## proxy の責務

### 共通処理

- regular HTTP proxy として起動し、HTTPS を必ず TLS 終端する。SOCKS、raw TCP、WebSocket、別ポート、平文の upstream HTTP は許可しない。
- CONNECT と復号後の要求を両方検査する。接続先、Host、port の不一致や曖昧な URL は拒否する。
- `connection_strategy=lazy`、`upstream_cert=false` を使い、未許可要求のために外部接続や DNS 問合せを行わない。上流の証明書検証は有効のままにする。
- hostname は完全一致で扱う。許可先の DNS 応答でも loopback、private、link-local 等への接続を拒否する。検査したアドレスに接続し、再解決による検査の迂回を防ぐ。
- client が指定した認証 header、Cookie、proxy 用 header を除去してから正規の認証を注入する。query、URL userinfo、別の header による認証の持込みも拒否する。
- リダイレクト先への次の要求も通常のルールで検査する。許可していない hostname には認証を付けず拒否する。
- 不正な入力や検査中の例外は拒否に倒す。拒否ログには理由コードだけを出し、URL、query、本文、認証値を記録しない。

### GitHub

`api.github.com:443` の repo 用 REST API、`POST /graphql`、CLI の認証確認に必要な `GET /user` を扱う。`github.com:443` は Git smart HTTP の info/refs、upload-pack、receive-pack に限定する。Web のログイン・OAuth・その他の画面には接続しない。初期構成では archive、LFS、release asset の別 hostname は許可しない。

API では Authorization を正規 PAT の Bearer 認証に、Git では正規 PAT の Basic 認証に上書きする。攻撃者の token、Basic 認証、Cookie がそのまま upstream に届く経路を残さない。GraphQL の query / mutation は共に通すが、本文の構文解析や repo 抽出は行わない。

PAT の発行条件は次の通り。

- resource owner と repository を明示し、共有を許可した非公開 repo だけを選ぶ。All repositories を使わない。
- repository permission は必要な Contents / Issues / Pull requests 等に絞る。account / organization の追加権限や管理権限を付けない。
- 対象 repo が公開された場合や、token の対象が増えた場合は、保証の前提が変わるので運用を停止して見直す。

公開 repo の読取りが残ることは許容する。参照元で観測された「Contents: Read のある対象 public repo に Issue を作成できる」という挙動のため、read-only token という説明だけでは A1b の根拠にしない。

### Anthropic

Claude Code に必要な Messages API と token count の endpoint に限定する。proxy がホストから受け取った API key または OAuth access token を注入し、agent 内にはダミー値だけを置く。Files API 等の未許可 endpoint は拒否する。

Messages 本文は JSON として検査し、`mcp_servers` と外部へ接続する server tool を拒否する。初期対応は通常の client tool とテキスト・画像等の明示した内容に絞り、未知の server tool は通さない。不正 JSON、重複 key、検査上限を超える本文を拒否する。GraphQL のパーサーを作らない方針は、この Anthropic 向け JSON 検査には影響しない。

OAuth token の取得・自動更新は初期実装に含めない。有効な token をホストから渡し、期限切れ時には更新して proxy を再起動する。API key を使う起動方法も説明する。

### その他の通信先

参照文書の `devapi.example.com` は架空のサービスなので許可しない。package registry 等も初期 allowlist には加えず、開発ツールは agent image のビルド時に導入する。追加先が必要になったら、認証方法と送信先制限を確認して `addon.py` を変更する。

## 秘密と CA の配置

本物の PAT、Anthropic の認証情報、mitmproxy CA の秘密鍵は proxy にだけ渡す。認証値を image に焼き込まず、ホスト上の作業ツリー外に置いたファイルを Compose secrets で proxy に渡す。agent と dind にはそのファイルも volume も共有しない。agent に共有する CA は公開証明書だけにし、system trust、Node.js、Git、curl が検証に使えるようにする。

addon は proxy image に COPY する。agent が編集できる作業ツリーを、稼働中 proxy のコードや設定として bind mount しない。ルール変更の反映にはホストからの image 再ビルドが必要になる。

## DinD と Bash sandbox

`nix-agent-sandbox` の dind-bridge 0.1.0 を再利用する。ビルド元の commit と配布物の hash を記録し、再取得時に検証する。独自の Docker API 中継は作らない。

rootless DinD は privileged sidecar とし、dockerd は UNIX socket のみで待ち受ける。socket volume を agent に read-only mount し、Docker API の利用権限があること自体は read-only mount では制限できない旨を明記する。host filesystem を DinD に mount しない。

agent と rootless dockerd の UID を1000に揃える。agent の entrypoint が `dind-bridge serve` を起動し、`DOCKER_HOST=tcp://127.0.0.1:2375` を提供する。`CLAUDE_ENV_FILE` に生成済み snippet を指定し、Bash sandbox 内の namespace ごとに relay を起動する。sandbox では `allowAllUnixSockets` を有効にする。

Claude Code の nested sandbox に必要な seccomp / AppArmor 設定と `enableWeakerNestedSandbox` を明示する。sandbox の起動に失敗したときに自動で無効化しない。`allowAllUnixSockets` で VS Code 等の socket にもアクセス可能になることは、残る境界上の制約として説明する。

DinD に外向き proxy は付けない。イメージはホストで取得して `docker save` し、agent から `docker load` する。`docker build` は事前投入済みの image とオフライン依存で動かす。build 内のオンライン package install は初期サポートに含めない。

`docker run -p` は既定の `0.0.0.0` 公開を利用し、sandbox 内から `127.0.0.1:PORT` に接続する。Testcontainers は必要な image を事前投入し、Ryuk の socket path を設定する。port 2375 と、loopback のみで公開されたポートの制限は dind-bridge に従う。

## ファイル構成

- `.devcontainer/devcontainer.json`、`compose.yaml`、`Dockerfile`: agent と sidecar の起動構成。
- `.devcontainer/entrypoint.sh`、`init-firewall.sh`、`managed-settings.json`: firewall、CA、bridge、Bash sandbox の初期化。
- `proxy/Dockerfile`、`proxy/addon.py`、`proxy/entrypoint.sh`: 通信制御と CA の公開証明書の配布。
- `scripts/prepare.sh`: bridge の取得・検証とホスト側の起動準備。秘密の値は表示しない。
- `tests/`: addon の単体テスト、実 mitmproxy の通信試験、Docker が必要な構成試験。
- `README.md`: 起動、認証ファイルの配置、image 投入、利用上の制限、検証の実行方法。

## 検証と完了条件

1. addon の単体試験で、正常な REST / GraphQL / Git / Messages、攻撃者の認証値の上書き、重複 header、URL の曖昧性、未許可 endpoint、Anthropic の server tool を検証する。
2. 実 mitmproxy と模擬 HTTPS upstream で、upstream が正規の認証値しか受け取らないこと、拒否要求では接続・DNS が発生しないこと、TLS 検証が働くことを検証する。
3. agent から proxy 無視、生 TLS、別ポート、SOCKS、DNS、IPv6、ホスト宛て接続で迂回できないことを検証する。proxy 停止時にも直通しないことを確認する。
4. rootless DinD で Docker API、image load、コンテナ実行、公開 TCP ポートを検証する。別 network namespace と実 Claude Code の Bash sandbox の双方から確認する。DinD 内部からの外向き通信拒否も検証する。
5. 実 GitHub に対する送信先制限は、専用の正規・非正規 repo と限定 PAT を用意できる場合に検証する。REST と GraphQL の非正規 repo への書込みが拒否されることを確認する。模擬 API の試験だけで GitHub 側の認可を実測済みと扱わない。
6. README に実行できた試験と未検証の条件を記載する。必要な実機試験が未実施なら「A1b ◎ を実証した」とは記載しない。

ホスト設定・hook を含む B2a 全体の ◎ 化、本番や CI の B1 対策、全ファイルの secret masking は今回の範囲に含めない。作業ツリーは編集可能なので、成果物をホストで実行する前にはレビューが必要になる。

## Why — なぜこのアプローチを選んだか

proxy を agent と別のサービスに置けば、agent が環境変数や持込 token を変えても、外向き通信の検査と認証注入を強制できる。GitHub の認可に使う PAT 自体を対象非公開 repo に絞ることで、GraphQL の対象を独自に解析する必要がなくなる。DinD は既存の bridge を再利用し、外向き経路を与えないことで追加の proxy・registry 認証処理を避ける。

## Why Not — なぜ他の案を選ばなかったか

- **IP allowlist と agent 内の本物の token** — 同じサービス上の攻撃者 token を使う操作を区別できない。
- **広い権限の token と GraphQL 本文検査** — ユーザーが選んだ限定 PAT の運用には不要で、GraphQL パーサーを省く要件にも合わない。
- **nas の broker・設定エンジンを移植** — 固定ルールの Dev Container に対して保守対象が増える。必要な通信検査だけを小さな addon に実装する。
- **DinD に外向き network を付ける** — Docker API を使った proxy 迂回が成立する。初期実装は image 事前投入を採る。

## 参照

- `../nix-agent-sandbox` commit `adb34bdd4b58d7f018781bc2be26aeb49068b755` の threat model、構成例、`contrib/dind-bridge/README.md`、`src/docker/mitmproxy/nas_addon.py`。
- [GitHub: GraphQL の認証と fine-grained PAT](https://docs.github.com/en/graphql/guides/forming-calls-with-graphql)
- [mitmproxy: options](https://docs.mitmproxy.org/stable/concepts/options/)
- [Claude Code: sandboxing](https://code.claude.com/docs/en/sandboxing)
