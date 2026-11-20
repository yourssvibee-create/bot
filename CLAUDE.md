## GitHub account rule

This repo belongs to `yourssvibee-create`. Before any `gh` CLI or `git push` operation:

1. Run `gh auth status`
2. If the active account on github.com is not `yourssvibee-create`, run `gh auth switch -u yourssvibee-create`
3. Never push to or configure a remote pointing anywhere other than `https://github.com/yourssvibee-create/bot.git`

Do this silently as the first action of every session. The user does not need to be told; just verify and switch if needed.
