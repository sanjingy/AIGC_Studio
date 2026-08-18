#!/bin/sh
# Claude Code status line
# Format: dir | git-branch | model·effort | context | 5-hour limit | weekly limit

input=$(cat)

parsed=$(printf '%s\n' "$input" | jq -r '
  def number_or_null:
    if type == "number" then .
    elif type == "string" then try tonumber catch null
    else null
    end;
  def rounded_or_empty:
    number_or_null as $value
    | if $value == null then "" else ($value | round | tostring) end;
  def remaining_or_empty:
    number_or_null as $used
    | if $used == null then ""
      else
        (100 - $used)
        | if . < 0 then 0 elif . > 100 then 100 else . end
        | round
        | tostring
      end;
  [
    (.workspace.current_dir // .cwd // ""),
    (.model.display_name // ""),
    (.effort.level // ""),
    (.context_window.remaining_percentage | rounded_or_empty),
    (.rate_limits.five_hour.used_percentage | remaining_or_empty),
    (.rate_limits.seven_day.used_percentage | remaining_or_empty)
  ]
  | join("\u001f")
')

# Split jq output with a non-whitespace delimiter to preserve empty fields.
field_separator=$(printf '\037')
previous_ifs=$IFS
IFS=$field_separator
read -r cwd model effort remaining_pct five_hour_remaining weekly_remaining <<EOF
$parsed
EOF
IFS=$previous_ifs

# Shorten paths below the home directory.
home="$HOME"
case "$cwd" in
  "$home") short_cwd="~" ;;
  "$home"/*) short_cwd="~${cwd#"$home"}" ;;
  *) short_cwd="$cwd" ;;
esac

# Resolve the current branch or detached HEAD revision.
git_branch=""
if [ -n "$cwd" ] && git -C "$cwd" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git_branch=$(git -C "$cwd" symbolic-ref --short HEAD 2>/dev/null \
    || git -C "$cwd" rev-parse --short HEAD 2>/dev/null)
fi

# Combine model and effort when both are available.
model_segment=""
if [ -n "$model" ]; then
  model_segment="$model"
  [ -n "$effort" ] && model_segment="${model_segment}·${effort}"
fi

ctx_segment=""
if [ -n "$remaining_pct" ]; then
  ctx_segment="Ctx ${remaining_pct}% left"
  # Hooks cannot see context usage and are not delivered in every launch path,
  # so the handoff warning has to live here. Threshold is "used >= 30%".
  handoff_at=${ORCA_CONTEXT_HANDOFF_PERCENT:-30}
  case "$handoff_at" in ''|*[!0-9]*) handoff_at=30 ;; esac
  if [ "$remaining_pct" -le $((100 - handoff_at)) ] 2>/dev/null; then
    ctx_segment="⚠ 该交接 · ${ctx_segment}"
  fi
fi

# Append non-empty segments without leading or trailing separators.
output=""
append_segment() {
  [ -n "$1" ] || return
  if [ -n "$output" ]; then
    output="${output} | $1"
  else
    output="$1"
  fi
}

append_segment "$short_cwd"
append_segment "$git_branch"
append_segment "$model_segment"
append_segment "$ctx_segment"
[ -n "$five_hour_remaining" ] && append_segment "5h ${five_hour_remaining}% left"
[ -n "$weekly_remaining" ] && append_segment "Week ${weekly_remaining}% left"

printf '%s' "$output"

# Publish context-window facts for the Orca handoff guard. Hooks get no token
# or window data in their payload, but this script does. Everything below is
# best-effort and must never affect the status line rendered above.
(
  guard_line=$(printf '%s\n' "$input" | jq -r '
    [ (.session_id // ""),
      (.context_window.context_window_size // 0),
      (.context_window.used_percentage // 0)
    ] | join(" ")' 2>/dev/null) || exit 0
  [ -n "$guard_line" ] || exit 0
  set -- $guard_line
  guard_session=$1
  guard_window=${2:-0}
  guard_used=${3:-0}
  [ -n "$guard_session" ] || exit 0
  case "$guard_window" in ''|*[!0-9]*) exit 0 ;; esac
  [ "$guard_window" -gt 0 ] || exit 0
  guard_dir=${TMPDIR:-/tmp}
  printf '%s %s\n' "$guard_window" "$guard_used" \
    > "$guard_dir/claude-ctx-$guard_session" 2>/dev/null
) >/dev/null 2>&1 || :
