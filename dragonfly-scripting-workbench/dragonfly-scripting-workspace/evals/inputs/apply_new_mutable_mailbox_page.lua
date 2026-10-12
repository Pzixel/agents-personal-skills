local NATIVE_CHUNK_ITEMS = 256
local MAX_RECORDS = 256
local MAX_GRANTS = 8192
local MAX_OLD_GRANTS = 8192
local MAX_AFFECTED_WORKSPACES = 4096
local MAX_AFFECTED_LABELS = 4000
local MAX_REQUEST_BYTES = 16 * 1024 * 1024
local MAX_REQUEST_ARGUMENTS = 65536
local MAX_PLAN_BYTES = 32 * 1024 * 1024
local RECEIPT_HEADER_FIELD = 'page_receipt_header'
local RECEIPT_PLAN_FIELD = 'page_receipt_plan'
local RECEIPT_REMOVE_FIELD = 'page_receipt_remove'
local AUTHENTICATION_FAILURES_KEY =
  'imap:{inbound}:authentication_failures'
local EMPTY_STREAKS_KEY =
  'imap:{inbound}:empty_streaks'

-- Key layout (n = record_count).
-- The caller binds the keys in this order;
-- every index below is a KEYS position:
--   1..20        catalog control, record, grant, label, and pin keys
--   21..20 + n   the INBOX cursor key of each record, in record order
--   21 + n       mailbox authentication failures
--   22 + n       candidate generation scope mailbox members
--   23 + n       candidate generation scope mailbox rows
--   24 + n       mailbox empty streaks
-- A separate resume script replays a stored plan and binds only keys
-- 1..21 + n. The stored plan must
-- therefore use no key index above 21 + n. Commands on keys 22 + n to 24 + n
-- are idempotent and run before the receipt becomes visible.

local function is_integer(value)
  return type(value) == 'number' and value >= 0 and math.floor(value) == value
end

local function is_lower_hex(value, length)
  return type(value) == 'string'
    and string.len(value) == length
    and string.match(value, '^[0-9a-f]+$') ~= nil
end

local function is_uuid(value)
  if type(value) ~= 'string' or string.len(value) ~= 36
    or string.sub(value, 9, 9) ~= '-'
    or string.sub(value, 14, 14) ~= '-'
    or string.sub(value, 19, 19) ~= '-'
    or string.sub(value, 24, 24) ~= '-' then
    return false
  end
  return is_lower_hex(string.gsub(value, '-', ''), 32)
end

local function cursor_key(mailbox_id)
  return 'imap:{inbound}:cursor:' .. mailbox_id .. ':INBOX'
end

local function decode_array(raw)
  if not raw then
    return nil
  end
  local ok, value = pcall(cjson.decode, raw)
  if not ok or type(value) ~= 'table' then
    return nil
  end
  local count = 0
  for index, _ in pairs(value) do
    if type(index) ~= 'number' or index < 1 or math.floor(index) ~= index
      or index > #value then
      return nil
    end
    count = count + 1
  end
  if count ~= #value then
    return nil
  end
  return value
end

local function grant_workspace(field, mailbox_id)
  if type(field) ~= 'string' then
    return nil
  end
  local raw = string.match(field, '^(-?%d+):' .. mailbox_id .. ':')
  local workspace = tonumber(raw)
  if not workspace or math.floor(workspace) ~= workspace
    or raw ~= string.format('%.0f', workspace) then
    return nil
  end
  return workspace
end

local function scope_entry(field, payload, mailbox_id)
  local workspace = grant_workspace(field, mailbox_id)
  local public_id = string.match(field, ':' .. mailbox_id .. ':([0-9a-f]+)$')
  local ok, grant = pcall(cjson.decode, payload)
  if not workspace or not is_lower_hex(public_id, 32) or not ok
    or type(grant) ~= 'table' or grant.workspace_id ~= workspace or grant.legacy_grant_id ~= public_id or type(grant.mailbox_address) ~= 'string'
    or string.find(grant.mailbox_address, '\0', 1, true) then return nil end
  return public_id, public_id .. '\0' .. string.format('%.0f', workspace) .. '\0' .. grant.mailbox_address
end

local function request_bytes()
  local total = 0
  for _, value in ipairs(ARGV) do
    total = total + string.len(value)
  end
  for _, value in ipairs(KEYS) do
    total = total + string.len(value)
  end
  return total
end

local function authorized()
  if redis.call('GET', KEYS[1]) ~= ARGV[1]
    or redis.call('GET', KEYS[2]) ~= ARGV[3] then
    return false
  end
  local storage_format = redis.call('HGET', KEYS[3], 'storage_format')
  local state = redis.call('HGET', KEYS[3], 'state')
  local mode = redis.call('HGET', KEYS[4], 'mode')
  if not storage_format and not state then
    return mode == 'migration'
      and redis.call('HGET', KEYS[4], 'generation') == ARGV[2]
      and redis.call('HGET', KEYS[4], 'expected_active') == ARGV[3]
  end
  return storage_format == 'mutable_v2'
    and state == 'applying'
    and redis.call('HGET', KEYS[3], 'generation') == ARGV[2]
    and mode == 'delta'
    and redis.call('HGET', KEYS[4], 'generation') == ARGV[2]
    and redis.call('HGET', KEYS[4], 'expected_active') == ARGV[3]
end

local function chunked_call(operation, key, values)
  local replies = {}
  local first = 1
  while first <= #values do
    local last = math.min(first + NATIVE_CHUNK_ITEMS - 1, #values)
    local chunk = {}
    for index = first, last do
      chunk[#chunk + 1] = values[index]
    end
    local result = redis.call(operation, key, unpack(chunk))
    for _, value in ipairs(result) do
      replies[#replies + 1] = value
    end
    first = last + 1
  end
  return replies
end

local function mget_dynamic(key_indices)
  local replies = {}
  local first = 1
  while first <= #key_indices do
    local last = math.min(first + NATIVE_CHUNK_ITEMS - 1, #key_indices)
    local chunk = {}
    for index = first, last do
      chunk[#chunk + 1] = KEYS[key_indices[index]]
    end
    local result = redis.call('MGET', unpack(chunk))
    for _, value in ipairs(result) do
      replies[#replies + 1] = value
    end
    first = last + 1
  end
  return replies
end

local function add_paired_commands(commands, operation, key_index, rows)
  local first = 1
  while first <= #rows do
    local last = math.min(first + NATIVE_CHUNK_ITEMS - 1, #rows)
    local arguments = {}
    for index = first, last do
      arguments[#arguments + 1] = rows[index][1]
      arguments[#arguments + 1] = rows[index][2]
    end
    commands[#commands + 1] = {op = operation, key = key_index, args = arguments}
    first = last + 1
  end
end

local function add_single_commands(commands, operation, key_index, values)
  local first = 1
  while first <= #values do
    local last = math.min(first + NATIVE_CHUNK_ITEMS - 1, #values)
    local arguments = {}
    for index = first, last do
      arguments[#arguments + 1] = values[index]
    end
    commands[#commands + 1] = {op = operation, key = key_index, args = arguments}
    first = last + 1
  end
end

local function add_mset_missing_commands(commands, key_indices, values)
  local first = 1
  while first <= #key_indices do
    local last = math.min(first + NATIVE_CHUNK_ITEMS - 1, #key_indices)
    local keys = {}
    local chunk_values = {}
    for index = first, last do
      keys[#keys + 1] = key_indices[index]
      chunk_values[#chunk_values + 1] = values[index]
    end
    commands[#commands + 1] = {
      op = 'MSET_MISSING',
      keys = keys,
      values = chunk_values
    }
    first = last + 1
  end
end

local function execute_commands(commands)
  for _, command in ipairs(commands) do
    if command.op == 'MSET_MISSING' then
      for index, key_index in ipairs(command.keys) do
        redis.call('SET', KEYS[key_index], command.values[index], 'NX')
      end
    elseif command.op == 'ZADD_NX' then
      redis.call('ZADD', KEYS[command.key], 'NX', unpack(command.args))
    else
      redis.call(command.op, KEYS[command.key], unpack(command.args))
    end
  end
end

if #ARGV < 4 then
  return {'protocol'}
end
local record_count = tonumber(ARGV[4])
if not is_integer(record_count) or record_count < 1 then
  return {'protocol'}
end
if record_count > MAX_RECORDS then
  return {'over_budget'}
end
if #KEYS ~= 24 + record_count
  or KEYS[21 + record_count] ~= AUTHENTICATION_FAILURES_KEY
  or KEYS[24 + record_count] ~= EMPTY_STREAKS_KEY then
  return {'protocol'}
end
if not is_uuid(ARGV[2]) or not is_uuid(ARGV[3]) then
  return {'protocol'}
end
if #KEYS + #ARGV + 3 > MAX_REQUEST_ARGUMENTS
  or request_bytes() > MAX_REQUEST_BYTES then
  return {'over_budget'}
end
if not authorized() then
  return {'fenced'}
end
local pending_receipt = redis.call(
  'HMGET',
  KEYS[4],
  RECEIPT_HEADER_FIELD,
  RECEIPT_PLAN_FIELD,
  RECEIPT_REMOVE_FIELD
)
if pending_receipt[1] or pending_receipt[2] or pending_receipt[3] then
  return {'receipt_pending'}
end

local records = {}
local ids = {}
local seen_mailboxes = {}
local total_grants = 0
local argument = 5
for record_number = 1, record_count do
  local mailbox_id = ARGV[argument]
  local record_version = ARGV[argument + 1]
  local record_json = ARGV[argument + 2]
  local cursor_json = ARGV[argument + 3]
  local label = ARGV[argument + 4]
  local grant_fields_json = ARGV[argument + 5]
  local grant_count = tonumber(ARGV[argument + 6])
  argument = argument + 7
  if not is_lower_hex(mailbox_id, 64)
    or not is_lower_hex(record_version, 64)
    or record_json == '' or cursor_json == '' or label == ''
    or not is_integer(grant_count) or grant_count < 1
    or seen_mailboxes[mailbox_id]
    or KEYS[20 + record_number] ~= cursor_key(mailbox_id) then
    return {'protocol'}
  end
  seen_mailboxes[mailbox_id] = true
  total_grants = total_grants + grant_count
  if total_grants > MAX_GRANTS then
    return {'over_budget'}
  end
  local grant_fields = decode_array(grant_fields_json)
  if not grant_fields or #grant_fields ~= grant_count then
    return {'protocol'}
  end
  local grants = {}
  local seen_fields = {}
  for grant_number = 1, grant_count do
    local field = ARGV[argument]
    local payload = ARGV[argument + 1]
    local workspace = tonumber(ARGV[argument + 2])
    argument = argument + 3
    if type(field) ~= 'string' or field == ''
      or field ~= grant_fields[grant_number]
      or payload == ''
      or not is_integer(workspace)
      or grant_workspace(field, mailbox_id) ~= workspace
      or seen_fields[field] then
      return {'protocol'}
    end
    seen_fields[field] = true
    grants[grant_number] = {
      field = field,
      payload = payload,
      workspace = workspace
    }
  end
  ids[record_number] = mailbox_id
  records[record_number] = {
    mailbox_id = mailbox_id,
    record_version = record_version,
    record_json = record_json,
    cursor_json = cursor_json,
    label = label,
    grant_fields_json = grant_fields_json,
    grants = grants
  }
end
if argument - 1 ~= #ARGV then
  return {'protocol'}
end

local apply_mode = redis.call('HGET', KEYS[4], 'mode')
local legacy_versions = {}
local migration_pending_ids = {}
local migration_preserve_due_ids = {}
if apply_mode == 'migration' then
  legacy_versions = chunked_call('HMGET', KEYS[18], ids)
  for record_number, record in ipairs(records) do
    local legacy_version = legacy_versions[record_number]
    if legacy_version and not is_lower_hex(legacy_version, 64) then
      return {'protocol'}
    end
    if legacy_version == record.record_version then
      migration_preserve_due_ids[#migration_preserve_due_ids + 1] =
        record.mailbox_id
    else
      migration_pending_ids[#migration_pending_ids + 1] = record.mailbox_id
    end
  end
end

local active_versions = chunked_call('HMGET', KEYS[5], ids)
local additions = {}
local update_indices = {}
local affected = {}
for record_number, record in ipairs(records) do
  local active_version = active_versions[record_number]
  if active_version and not is_lower_hex(active_version, 64) then
    return {'protocol'}
  end
  if not active_version then
    additions[#additions + 1] = record_number
    affected[#affected + 1] = record_number
  elseif active_version ~= record.record_version then
    record.old_version = active_version
    update_indices[#update_indices + 1] = record_number
    affected[#affected + 1] = record_number
  end
end

if #affected == 0 then
  local commands = {}
  add_single_commands(commands, 'SREM', 17, migration_preserve_due_ids)
  add_single_commands(commands, 'SADD', 17, migration_pending_ids)
  local preserve_due_rows = {}
  for _, mailbox_id in ipairs(migration_preserve_due_ids) do
    preserve_due_rows[#preserve_due_rows + 1] = {0, mailbox_id}
  end
  add_paired_commands(commands, 'ZADD_NX', 14, preserve_due_rows)
  execute_commands(commands)
  return {'applied', '0', '0'}
end

local record_fields = {}
local grant_fields = {}
local grant_payloads = {}
local unique_grant_fields = {}
for _, record_number in ipairs(affected) do
  local record = records[record_number]
  record.record_field = record.mailbox_id .. ':' .. record.record_version
  record_fields[#record_fields + 1] = record.record_field
  for _, grant in ipairs(record.grants) do
    if unique_grant_fields[grant.field] then
      return {'protocol'}
    end
    unique_grant_fields[grant.field] = grant
    grant_fields[#grant_fields + 1] = grant.field
    grant_payloads[#grant_payloads + 1] = grant.payload
  end
end
local existing_records = chunked_call('HMGET', KEYS[6], record_fields)
for index, existing in ipairs(existing_records) do
  local record = records[affected[index]]
  if existing and existing ~= record.record_json then
    return {'record_conflict'}
  end
end
local existing_grants = chunked_call('HMGET', KEYS[7], grant_fields)
for index, existing in ipairs(existing_grants) do
  if existing and existing ~= grant_payloads[index] then
    return {'record_conflict'}
  end
end

local update_ids = {}
for _, record_number in ipairs(update_indices) do
  update_ids[#update_ids + 1] = records[record_number].mailbox_id
end
local old_grant_fields_json = chunked_call('HMGET', KEYS[11], update_ids)
local old_labels = chunked_call('HMGET', KEYS[12], update_ids)
local old_grant_fields = {}
local old_grant_field_set = {}
local old_workspace_counts = {}
local old_grant_count = 0
local label_removals = {}
local scope_remove_ids, scope_remove_members = {}, {}
local scope_add_rows, scope_add_members = {}, {}
for update_number, record_number in ipairs(update_indices) do
  local record = records[record_number]
  local fields = decode_array(old_grant_fields_json[update_number])
  local old_label = old_labels[update_number]
  if not fields or type(old_label) ~= 'string' or old_label == '' then
    return {'protocol'}
  end
  record.old_grant_fields = fields
  record.old_label = old_label
  label_removals[old_label] = (label_removals[old_label] or 0) + 1
  for _, field in ipairs(fields) do
    local workspace = grant_workspace(field, record.mailbox_id)
    if not workspace or old_grant_field_set[field] then
      return {'protocol'}
    end
    old_grant_field_set[field] = true
    old_grant_fields[#old_grant_fields + 1] = field
    -- A grant listed for the mailbox but absent from the grant hash was
    -- already removed (a half-removed mailbox). Nothing remains to count or
    -- to remove from the scope. The update below writes the grants of the
    -- new record. This branch holds because `HDEL 7` and `ZREM 8` stay
    -- adjacent in one command list, and because the scope patch on keys
    -- 22 + n and 23 + n runs before any grant hash removal.
    local old_payload = redis.call('HGET', KEYS[7], field)
    if old_payload then
      if apply_mode == 'delta' then
        local public_id, member = scope_entry(field, old_payload, record.mailbox_id)
        if not public_id then return {'protocol'} end
        scope_remove_ids[#scope_remove_ids + 1], scope_remove_members[#scope_remove_members + 1] = public_id, member
      end
      old_workspace_counts[workspace] = (old_workspace_counts[workspace] or 0) + 1
    end
    old_grant_count = old_grant_count + 1
    if old_grant_count > MAX_OLD_GRANTS then
      return {'over_budget'}
    end
  end
end

local label_additions = {}
local new_workspace_counts = {}
local new_workspace_set = {}
local addition_due_ids = migration_preserve_due_ids
local update_due_ids = {}
local pending_ids = migration_pending_ids
local cursor_key_indices = {}
local cursor_values = {}
for _, record_number in ipairs(additions) do
  local record = records[record_number]
  if apply_mode ~= 'migration' then
    addition_due_ids[#addition_due_ids + 1] = record.mailbox_id
  end
  cursor_key_indices[#cursor_key_indices + 1] = 20 + record_number
  cursor_values[#cursor_values + 1] = record.cursor_json
end
for _, record_number in ipairs(affected) do
  local record = records[record_number]
  label_additions[record.label] = (label_additions[record.label] or 0) + 1
  for _, grant in ipairs(record.grants) do
    new_workspace_counts[grant.workspace] =
      (new_workspace_counts[grant.workspace] or 0) + 1
    new_workspace_set[grant.workspace] = true
  end
  if apply_mode ~= 'migration' and record.old_version then
    update_due_ids[#update_due_ids + 1] = record.mailbox_id
  end
end

local label_set = {}
for label, _ in pairs(label_additions) do
  label_set[label] = true
end
for label, _ in pairs(label_removals) do
  label_set[label] = true
end
local labels = {}
for label, _ in pairs(label_set) do
  labels[#labels + 1] = label
end
table.sort(labels)
if #labels > MAX_AFFECTED_LABELS then
  return {'over_budget'}
end
local current_label_counts = chunked_call('HMGET', KEYS[10], labels)
local label_rows = {}
local label_deletes = {}
for index, label in ipairs(labels) do
  local current = 0
  if current_label_counts[index] then
    current = tonumber(current_label_counts[index])
    if not is_integer(current) or current < 1 then
      return {'protocol'}
    end
  end
  local removals = label_removals[label] or 0
  if current < removals then
    return {'protocol'}
  end
  local target = current - removals + (label_additions[label] or 0)
  if target > 0 then
    label_rows[#label_rows + 1] = {label, tostring(target)}
  else
    label_deletes[#label_deletes + 1] = label
  end
end

local affected_workspace_set = {}
for workspace, _ in pairs(old_workspace_counts) do
  affected_workspace_set[workspace] = true
end
for workspace, _ in pairs(new_workspace_counts) do
  affected_workspace_set[workspace] = true
end
local affected_workspaces = {}
for workspace, _ in pairs(affected_workspace_set) do
  affected_workspaces[#affected_workspaces + 1] = workspace
end
table.sort(affected_workspaces)
if #affected_workspaces > MAX_AFFECTED_WORKSPACES then
  return {'over_budget'}
end
local workspace_deletes = {}
for _, workspace in ipairs(affected_workspaces) do
  local current = redis.call('ZCOUNT', KEYS[8], workspace, workspace)
  local removals = old_workspace_counts[workspace] or 0
  if current < removals then
    return {'protocol'}
  end
  local target = current - removals + (new_workspace_counts[workspace] or 0)
  if target == 0 then
    workspace_deletes[#workspace_deletes + 1] = tostring(workspace)
  end
end

local cursor_existing = mget_dynamic(cursor_key_indices)
local missing_cursor_keys = {}
local missing_cursor_values = {}
for index, existing in ipairs(cursor_existing) do
  if not existing then
    missing_cursor_keys[#missing_cursor_keys + 1] = cursor_key_indices[index]
    missing_cursor_values[#missing_cursor_values + 1] = cursor_values[index]
  end
end

local commands = {}
local record_rows = {}
local grant_rows = {}
local membership_rows = {}
local workspace_rows = {}
local grant_field_rows = {}
local mailbox_label_rows = {}
local removed_ids = {}
local commit_rows = {}
local workspaces = {}
for workspace, _ in pairs(new_workspace_set) do
  workspaces[#workspaces + 1] = workspace
end
table.sort(workspaces)
for _, workspace in ipairs(workspaces) do
  workspace_rows[#workspace_rows + 1] = {workspace, tostring(workspace)}
end
for _, record_number in ipairs(affected) do
  local record = records[record_number]
  record_rows[#record_rows + 1] = {record.record_field, record.record_json}
  grant_field_rows[#grant_field_rows + 1] = {
    record.mailbox_id,
    record.grant_fields_json
  }
  mailbox_label_rows[#mailbox_label_rows + 1] = {record.mailbox_id, record.label}
  removed_ids[#removed_ids + 1] = record.mailbox_id
  commit_rows[#commit_rows + 1] = {record.mailbox_id, record.record_version}
  for _, grant in ipairs(record.grants) do
    grant_rows[#grant_rows + 1] = {grant.field, grant.payload}
    membership_rows[#membership_rows + 1] = {grant.workspace, grant.field}
    if apply_mode == 'delta' then
      local public_id, member = scope_entry(grant.field, grant.payload, record.mailbox_id)
      if not public_id then return {'protocol'} end
      scope_add_rows[#scope_add_rows + 1], scope_add_members[#scope_add_members + 1] = {public_id, member}, {0, member}
    end
  end
end

add_single_commands(commands, 'HDEL', 7, old_grant_fields)
add_single_commands(commands, 'ZREM', 8, old_grant_fields)
add_single_commands(commands, 'ZREM', 9, workspace_deletes)
add_paired_commands(commands, 'HSET', 7, grant_rows)
add_paired_commands(commands, 'ZADD', 8, membership_rows)
add_paired_commands(commands, 'ZADD', 9, workspace_rows)
add_paired_commands(commands, 'HSET', 6, record_rows)
add_paired_commands(commands, 'HSET', 11, grant_field_rows)
add_paired_commands(commands, 'HSET', 12, mailbox_label_rows)
add_paired_commands(commands, 'HSET', 10, label_rows)
add_single_commands(commands, 'HDEL', 10, label_deletes)
add_single_commands(commands, 'HDEL', 13, removed_ids)
add_single_commands(commands, 'SREM', 17, migration_preserve_due_ids)
add_single_commands(commands, 'SADD', 17, pending_ids)
local addition_due_rows = {}
for _, mailbox_id in ipairs(addition_due_ids) do
  addition_due_rows[#addition_due_rows + 1] = {0, mailbox_id}
end
add_paired_commands(commands, 'ZADD_NX', 14, addition_due_rows)
local update_due_rows = {}
for _, mailbox_id in ipairs(update_due_ids) do
  update_due_rows[#update_due_rows + 1] = {0, mailbox_id}
end
add_paired_commands(commands, 'ZADD', 14, update_due_rows)
add_single_commands(commands, 'HDEL', 21 + record_count, removed_ids)
if #update_indices > 0 then
  local now = redis.call('TIME')
  local now_ms = string.format(
    '%.0f',
    tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
  )
  local obsolete_rows = {}
  for _, record_number in ipairs(update_indices) do
    local record = records[record_number]
    obsolete_rows[#obsolete_rows + 1] = {
      now_ms,
      record.mailbox_id .. ':' .. record.old_version
    }
  end
  add_paired_commands(commands, 'ZADD', 16, obsolete_rows)
end
add_mset_missing_commands(commands, missing_cursor_keys, missing_cursor_values)

local commit = {}
add_paired_commands(commit, 'HSET', 5, commit_rows)
local header = {
  v = 1,
  kind = 'add',
  candidate = ARGV[2],
  expected_active = ARGV[3],
  ids = ids
}
local plan = {
  v = 1,
  kind = 'add',
  candidate = ARGV[2],
  expected_active = ARGV[3],
  ids = ids,
  commands = commands,
  commit = commit
}
local header_raw = cjson.encode(header)
local plan_raw = cjson.encode(plan)
if string.len(plan_raw) > MAX_PLAN_BYTES then
  return {'over_budget'}
end

-- Keys 22 + n to 24 + n are outside the replayable plan layout (see the key
-- layout note above), so every command on them runs here, before the receipt.
local scope_commands = {}
add_single_commands(scope_commands, 'HDEL', 24 + record_count, removed_ids)
add_single_commands(scope_commands, 'HDEL', 23 + record_count, scope_remove_ids)
add_single_commands(scope_commands, 'ZREM', 22 + record_count, scope_remove_members)
add_paired_commands(scope_commands, 'HSET', 23 + record_count, scope_add_rows)
add_paired_commands(scope_commands, 'ZADD', 22 + record_count, scope_add_members)
execute_commands(scope_commands)

-- The next HSET makes the catalog replay description visible or leaves no
-- catalog receipt at all; the prior unpublished scope and empty streak patch
-- is idempotent, so a resume does not repeat it.
redis.call(
  'HSET',
  KEYS[4],
  RECEIPT_HEADER_FIELD,
  header_raw,
  RECEIPT_PLAN_FIELD,
  plan_raw
)
execute_commands(commands)
execute_commands(commit)
redis.call('HDEL', KEYS[4], RECEIPT_HEADER_FIELD, RECEIPT_PLAN_FIELD)

local response = {'applied', tostring(#additions), tostring(#update_indices)}
return response
