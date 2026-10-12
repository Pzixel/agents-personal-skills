"""Loop detection is the rule engine's least mechanical pure decision: Lua ends
`repeat` with `until`, reuses `do` for non-loop blocks, and `elseif ... then`
must not open a frame. Everything else in lua_call_audit.py is table lookup.

Run: python3 -m pytest scripts/test_lua_call_audit.py
"""
from lua_call_audit import audit_source

TRICKY = """
local function helper(k)
  if k then
    return redis.call('GET', KEYS[1])
  elseif other then
    return nil
  end
end
do
  redis.call('SET', KEYS[2], 'not a loop')
end
local i = 0
repeat
  redis.call('INCR', KEYS[3])
  i = i + 1
until i > 3
local s = 'for x do redis.call(1) end'
for _, f in ipairs(fields) do
  if f then
    redis.call('HGET', KEYS[4], f)
  end
end
"""


def test_only_real_loop_bodies_are_flagged():
    lines = {(line, rule) for line, rule, _, _ in audit_source("t.lua", TRICKY)}
    assert lines == {(14, "call-in-loop"), (20, "batchable-hash")}


def test_key_built_from_argv_is_undeclared():
    src = "redis.call('HSET', 'catalog:' .. ARGV[1], 'f', ARGV[2])\n"
    assert [(f[0], f[1]) for f in audit_source("t.lua", src)] == [(1, "undeclared-key")]
