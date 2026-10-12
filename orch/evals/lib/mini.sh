# Sourced by each case's scaffold.sh (cwd = the run workspace).
# mini_copy copies the synthetic fixture into the workspace; mini_commit makes
# it a git repository with a local identity and one commit, so cases can grade
# commits and the run's own `git commit` works under the eval HOME.
MINI_FIXTURE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../fixtures/mini" && pwd)"

mini_copy() {
    cp -R "$MINI_FIXTURE"/. .
    find . -name __pycache__ -type d -prune -exec rm -rf {} +
}

mini_commit() {
    git init -q
    git config user.name "Eval Author"
    git config user.email "eval@example.invalid"
    git add -A
    git commit -qm init
}
