# TODO

現在の通信・隔離の範囲は[通信ルール](network-policy.md)、利用手順は[追加の利用手順](usage.md)を参照してください。

## 未対応

- [ ] Dev ContainersのReopenに数十秒かかる問題を調べ、VS Code拡張フォルダのvolume化を検討する。対象はホストのVS Codeから接続する場合で、todeの拡張はimageに組み込み済み。volume化する場合は、[再接続前にagentが変更した拡張を消す手順](usage.md#それでもvs-codeで接続する場合)と両立させる。
  - VS Codeを積極的に勧めるのが難しくなってしまったので優先度が低い

## 文書化済みの制約

- DinDはrootless dockerdでもprivilegedコンテナであり、カーネル経由のホストへの影響は残る。Dockerが不要なら無効にする。[コンテナ内の実行権限](network-policy.md#コンテナ内の実行権限)。
- ホストのVS Code接続では、ホストでのコマンド実行や外部通信の経路が残る。agent稼働中には接続しない運用とし、コンテナ内のエディタにはtodeを使う。[VS Codeで開く](usage.md#vs-codeで開く)。
