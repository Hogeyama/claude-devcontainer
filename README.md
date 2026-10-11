# Claude Code用Dev Containerのコア

このディレクトリはgit subtreeで取り込まれたものです。編集せず、カスタマイズは`.devcontainer/`直下の`claude.toml`、`claude.local.toml`（ホストだけの設定、コミットしない）、`devcontainer.json`、`compose.yaml`、`agent-setup.sh`で行ってください。`template/`はそれらのテンプレートです。

上流の修正は次のコマンドで取り込みます。使い方の詳細は[上流](./UPSTREAM)のREADMEを参照してください。

```sh
git subtree pull --prefix=.devcontainer/claude https://github.com/Hogeyama/claude-devcontainer dist --squash
```
