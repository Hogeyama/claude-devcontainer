# Claude Code用Dev Containerのコア

このディレクトリはgit subtreeで取り込まれたコアです。編集せず、カスタマイズは`.devcontainer/`直下の`devcontainer.json`、`compose.yaml`、`agent-setup.sh`で行ってください。`template/`はそれらの雛形です。

`UPSTREAM`には、このコアの元になった上流のコミットのURLが書かれています。

上流の修正は次のコマンドで取り込みます。使い方の詳細は[上流のREADME](https://github.com/Hogeyama/claude-devcontainer)を参照してください。

```sh
git subtree pull --prefix=.devcontainer/claude https://github.com/Hogeyama/claude-devcontainer dist --squash
```
