#!/usr/bin/env bash
# Публикация на GitHub: https://github.com/ALEXalesha/SortProgramm
#
#   bash tools/publish_github.sh            # только код
#   bash tools/publish_github.sh v4.1.0     # код и тег
#
# Источник правды - Gitea. На GitHub уезжает копия main, пересобранная заново при
# каждом запуске (отсюда --force). Рабочая история не трогается. В копии:
#
#   - личный gmail автора и коммитера заменён на анонимную почту GitHub;
#   - убраны файлы с именами из настоящей папки загрузок автора - это история
#     его загрузок, в открытый репозиторий она не идёт (решение автора,
#     23.09.2026): plan_preview.txt и снимок tests/fixtures/real_layout.json
#     удалены, overrides.json во всех коммитах пустой. Снимок в тестах без
#     файла пропускается с объяснением.
set -euo pipefail

export PATH="$PATH:/c/Program Files/GitHub CLI"
REPO=ALEXalesha/SortProgramm
# Личный адрес не записан здесь строкой: скрипт берёт его из настроек репозитория.
# Строкой он однажды уехал на GitHub внутри такого же скрипта.
PRIVATE_EMAIL="$(git config user.email)"
PUBLIC_EMAIL=203467574+ALEXalesha@users.noreply.github.com
DROPPED="plan_preview.txt tests/fixtures/real_layout.json"
TAG="${1:-}"

cd "$(dirname "$0")/.."

git remote get-url github >/dev/null 2>&1 || git remote add github "https://github.com/$REPO.git"

git branch -f github-main main
FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f --env-filter "
  if [ \"\$GIT_AUTHOR_EMAIL\" = '$PRIVATE_EMAIL' ]; then export GIT_AUTHOR_EMAIL='$PUBLIC_EMAIL'; fi
  if [ \"\$GIT_COMMITTER_EMAIL\" = '$PRIVATE_EMAIL' ]; then export GIT_COMMITTER_EMAIL='$PUBLIC_EMAIL'; fi
" github-main >/dev/null 2>&1
git update-ref -d refs/original/refs/heads/github-main 2>/dev/null || true

# Личные файлы - прямо в индексе каждого коммита, без выгрузки дерева на диск.
# Коммиты, в которых кроме них ничего не менялось, после этого пустые и уходят.
EMPTY_JSON=$(printf '{}\n' | git hash-object -w --stdin)
FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f --prune-empty --index-filter "
  git rm --cached --ignore-unmatch -q $DROPPED
  if git ls-files --error-unmatch overrides.json >/dev/null 2>&1; then
    git update-index --cacheinfo 100644,$EMPTY_JSON,overrides.json
  fi
" github-main >/dev/null 2>&1
git update-ref -d refs/original/refs/heads/github-main 2>/dev/null || true

# Личный адрес в содержимом файлов - по всей публикуемой истории.
# `|| true`: без совпадений git grep возвращает 1, и при pipefail скрипт молча
# обрывался бы здесь - так и было в репозитории, где почты в файлах нет вовсе.
DIRTY=$(git grep -I -l -F "$PRIVATE_EMAIL" $(git rev-list github-main) -- 2>/dev/null \
        | sed 's/^[^:]*://' | sort -u | tr '\n' ' ' || true)
if [ -n "$DIRTY" ]; then
  echo "личный адрес в файлах: $DIRTY- переписываю содержимое"
  FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f --index-filter "
    for f in $DIRTY; do
      mode=\$(git ls-tree \$GIT_COMMIT -- \"\$f\" | awk '{print \$1}')
      [ -n \"\$mode\" ] || continue
      blob=\$(git cat-file blob \$GIT_COMMIT:\"\$f\" \
             | sed 's/$PRIVATE_EMAIL/$PUBLIC_EMAIL/g' | git hash-object -w --stdin)
      git update-index --cacheinfo \$mode,\$blob,\"\$f\"
    done
  " github-main >/dev/null 2>&1
  git update-ref -d refs/original/refs/heads/github-main 2>/dev/null || true
fi

# Стражи: ни одно из этого не должно дойти до пуша.
if git log github-main --format='%ae%n%ce' | grep -qx "$PRIVATE_EMAIL"; then
  echo "в публикуемой ветке остался личный адрес в авторе коммита - пуш отменён" >&2
  exit 1
fi
if git grep -I -q -F "$PRIVATE_EMAIL" $(git rev-list github-main) -- 2>/dev/null; then
  echo "личный адрес остался в файлах публикуемой истории - пуш отменён" >&2
  exit 1
fi
LEFT=$(git log github-main --format= --name-only -- $DROPPED | sort -u)
if [ -n "$LEFT" ]; then
  echo "личные файлы остались в публикуемой истории - пуш отменён: $LEFT" >&2
  exit 1
fi
for rev in $(git rev-list github-main -- overrides.json); do
  blob=$(git rev-parse -q --verify "$rev:overrides.json" || true)
  if [ -n "$blob" ] && [ "$blob" != "$EMPTY_JSON" ]; then
    echo "overrides.json не пустой в $rev - пуш отменён" >&2
    exit 1
  fi
done

echo "ветка github-main: $(git rev-list --count github-main) коммитов, $(git log -1 --format=%h github-main)"
gh auth setup-git
git push github github-main:main --force

if [ -n "$TAG" ]; then
  # Тег ставится прямо на GitHub, без локального: локальный указывал бы на
  # переписанный коммит github-main и путался с тегами Gitea.
  git push github "+github-main:refs/tags/$TAG"
  echo "тег $TAG отправлен"
fi
