---
type: regex
target: trace
flags: m
pattern: '^(?=[^\n]*"subagent_type":"(?:orch:)?builder")(?=[^\n]*"parent_tool_use_id":null)[^\n]*$[\s\S]*?^(?=[^\n]*"subagent_type":"(?:orch:)?reviewer")(?=[^\n]*"model":"fable")(?=[^\n]*mode: review\b)(?=[^\n]*"parent_tool_use_id":null)[^\n]*$[\s\S]*?^(?=[^\n]*"subagent_type":"(?:orch:)?reviewer")(?=[^\n]*"model":"fable")(?=[^\n]*mode: review\b)(?=[^\n]*"parent_tool_use_id":null)[^\n]*$[\s\S]*?^(?=[^\n]*"subagent_type":"(?:orch:)?reviewer")(?=[^\n]*"model":"fable")(?=[^\n]*mode: crosscheck)(?=[^\n]*"parent_tool_use_id":null)[^\n]*$'
arm: with-only
---
