-- claim_mailbox_batch, HMGET variant (the Q7 measurement arm).
-- Same KEYS arity (14), same ARGV protocol, same reply shape as
-- claim_mailbox_batch.orig.lua. Two differences, both inside the candidate scan:
--   1. the per-candidate `HGET versions` / `HGET leases` become one chunked
--      HMGET per hash (chunk 512 fields);
--   2. the ~2 KB `HGET mailbox_records` moves out of the scan and runs only for
--      the <= batch_size candidates that are actually claimed (chunked HMGET).
-- KNOWN semantic difference: the original returns {'PROTOCOL','record_missing'}
-- if ANY eligible candidate in the window lacks a record, including candidates
-- past batch_size that it would never claim; this variant only checks the
-- claimed ones. On data where every eligible candidate has its record -- the
-- seeded state here and the invariant the writer maintains -- the replies are
-- identical, which is what the benchmark asserts.
local function server_now_millis()
  local now = redis.call('TIME')
  return tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
end

local function parse_lease(raw)
  if not raw then
    return nil
  end
  local token, deadline = string.match(raw, '^([0-9a-f%-]+):(%d+)$')
  if not token then
    return false
  end
  return {token = token, deadline = tonumber(deadline)}
end

local function parse_receipt(raw)
  if not raw then
    return nil
  end
  local mailbox_id, token, deadline =
    string.match(raw, '^([0-9a-f]+):([0-9a-f%-]+):(%d+)$')
  if not mailbox_id then
    return false
  end
  return {mailbox_id = mailbox_id, token = token, deadline = tonumber(deadline)}
end

local function delete_matching_poll_start(mailbox_id, token)
  local poll_start = redis.call('HGET', KEYS[10], mailbox_id)
  if poll_start and string.sub(poll_start, 1, string.len(token) + 1) == token .. ':' then
    redis.call('HDEL', KEYS[10], mailbox_id)
  end
end

local function delete_capture(token)
  redis.call('HDEL', KEYS[14], token)
end

local function chunked_hmget(key, ids, first, last)
  -- Prefetch ids[first..last] from a hash in chunks. `unpack` on Dragonfly's
  -- Lua 5.4 takes at most 8163 arguments (8164 -> "stack overflow"), so the
  -- chunk is kept well below that.
  local CHUNK = 512
  local out = {}
  local i = first
  while i <= last do
    local stop = i + CHUNK - 1
    if stop > last then stop = last end
    local args = {}
    for j = i, stop do
      args[#args + 1] = ids[j]
    end
    local vals = redis.call('HMGET', key, unpack(args))
    for j = 1, #vals do
      out[i + j - 1] = vals[j]
    end
    i = stop + 1
  end
  return out
end

local function earliest_due()
  local earliest = redis.call('ZRANGE', KEYS[6], 0, 0, 'WITHSCORES')
  return earliest[2] or ''
end

local function resolved_response(now, batch_size, results)
  local response = {'RESOLVED', tostring(now), earliest_due(), tostring(batch_size)}
  for index = 1, batch_size do
    local item = results[index]
    if not item then
      item = {'UNUSED', ARGV[8 + index], '', '', ''}
    end
    for field = 1, 5 do
      table.insert(response, item[field])
    end
  end
  return response
end

if ARGV[1] ~= '1' then
  return {'PROTOCOL', 'schema_version'}
end

local expected_generation = ARGV[2]
local actual_run_id = ARGV[3]
local process_id = ARGV[4]
local lease_ttl = tonumber(ARGV[5])
local candidate_window = tonumber(ARGV[6])
local freshness_limit = tonumber(ARGV[7])
local batch_size = tonumber(ARGV[8])
if expected_generation == '' or actual_run_id == '' or process_id == ''
  or lease_ttl ~= 120000
  or not candidate_window or candidate_window < 1
  or not freshness_limit or freshness_limit < 1
  or not batch_size or batch_size < 1 or batch_size > 32
  or #ARGV ~= 8 + batch_size then
  return {'PROTOCOL', 'arguments'}
end
for index = 1, batch_size do
  if ARGV[8 + index] == '' then
    return {'PROTOCOL', 'arguments'}
  end
end

local now = server_now_millis()
local recovered = {}
local has_receipt = false
for index = 1, batch_size do
  local request_token = ARGV[8 + index]
  local receipt = parse_receipt(redis.call('HGET', KEYS[8], request_token))
  if receipt == false then
    return {'PROTOCOL', 'receipt'}
  end
  if receipt then
    has_receipt = true
    if receipt.token ~= request_token then
      return {'PROTOCOL', 'receipt_token'}
    end
    local lease = parse_lease(redis.call('HGET', KEYS[7], receipt.mailbox_id))
    if lease == false then
      return {'PROTOCOL', 'lease'}
    end
    if not lease or lease.token ~= receipt.token or lease.deadline ~= receipt.deadline then
      return {'PROTOCOL', 'receipt_lease_mismatch'}
    end
    if receipt.deadline <= now then
      recovered[index] = {
        'RECEIPT_EXPIRED',
        request_token,
        receipt.mailbox_id,
        tostring(receipt.deadline),
        ''
      }
    else
      local captured = redis.call('HGET', KEYS[14], request_token)
      if not captured then
        return {'PROTOCOL', 'record_missing'}
      end
      recovered[index] = {
        'CLAIMED',
        request_token,
        receipt.mailbox_id,
        tostring(receipt.deadline),
        captured
      }
    end
  end
end
if has_receipt then
  return resolved_response(now, batch_size, recovered)
end

local active_generation = redis.call('GET', KEYS[1])
if active_generation ~= expected_generation then
  return {'FENCED', 'active_generation_changed'}
end
if redis.call('GET', KEYS[2]) ~= active_generation then
  return {'FENCED', 'cursor_coverage_mismatch'}
end
if redis.call('GET', KEYS[3]) ~= actual_run_id then
  return {'FENCED', 'server_run_reconciliation_required'}
end
local last_reconciliation = tonumber(redis.call('GET', KEYS[4]) or '')
if not last_reconciliation or last_reconciliation > now
  or now - last_reconciliation > freshness_limit then
  return {'FENCED', 'catalog_not_fresh'}
end

local storage_format = redis.call('HGET', KEYS[11], 'storage_format')
local state = redis.call('HGET', KEYS[11], 'state')
local versions_key = KEYS[5]
if storage_format then
  if storage_format == 'mutable_v2' then
    if state == 'applying' then
      return {'FENCED', 'catalog_mutation_in_progress'}
    end
    if state ~= 'committed' then
      return {'PROTOCOL', 'catalog_descriptor'}
    end
  else
    return {'PROTOCOL', 'catalog_descriptor'}
  end
  if redis.call('HGET', KEYS[11], 'generation') ~= active_generation then
    return {'FENCED', 'active_generation_changed'}
  end
  versions_key = KEYS[12]
elseif state then
  return {'PROTOCOL', 'catalog_descriptor'}
end

local candidates = redis.call(
  'ZRANGEBYSCORE',
  KEYS[6],
  '-inf',
  tostring(now),
  'LIMIT',
  0,
  candidate_window
)
local saw_active = false
local removals = {}
local rescores = {}
local claimable = {}
-- HMGET PREFETCH: one chunked HMGET per hash instead of one HGET per candidate.
local versions_all = chunked_hmget(versions_key, candidates, 1, #candidates)
local leases_all = chunked_hmget(KEYS[7], candidates, 1, #candidates)
for candidate_index, mailbox_id in ipairs(candidates) do
  local version = versions_all[candidate_index]
  local lease = parse_lease(leases_all[candidate_index])
  if lease == false then
    return {'PROTOCOL', 'lease'}
  end
  if not version then
    if not lease then
      table.insert(removals, mailbox_id)
    elseif lease.deadline <= now then
      local stale_receipt = parse_receipt(redis.call('HGET', KEYS[8], lease.token))
      if stale_receipt == false then
        return {'PROTOCOL', 'receipt'}
      end
      if not stale_receipt or stale_receipt.mailbox_id ~= mailbox_id
        or stale_receipt.token ~= lease.token
        or stale_receipt.deadline ~= lease.deadline then
        return {'PROTOCOL', 'receipt_lease_mismatch'}
      end
      return {
        'CLEANUP_REQUIRED',
        tostring(now),
        mailbox_id,
        lease.token,
        tostring(lease.deadline)
      }
    else
      table.insert(rescores, {mailbox_id, lease.deadline})
    end
  else
    saw_active = true
    if not lease or lease.deadline <= now then
      -- DEFERRED PAYLOAD: the ~2 KB record is fetched after the scan, only for
      -- the <= batch_size candidates that are actually claimed.
      local old_receipt = nil
      if lease then
        old_receipt = parse_receipt(redis.call('HGET', KEYS[8], lease.token))
        if old_receipt == false then
          return {'PROTOCOL', 'receipt'}
        end
        if old_receipt and (
          old_receipt.mailbox_id ~= mailbox_id
          or old_receipt.token ~= lease.token
          or old_receipt.deadline ~= lease.deadline
        ) then
          return {'PROTOCOL', 'receipt_lease_mismatch'}
        end
      end
      if #claimable < batch_size then
        table.insert(claimable, {
          mailbox_id = mailbox_id,
          version = version,
          old_lease = lease,
          old_receipt = old_receipt
        })
      end
    else
      table.insert(rescores, {mailbox_id, lease.deadline})
    end
  end
end

-- Payload read for the chosen candidates only, before any write, so the write
-- order and the 'record_missing' early return stay where the original had them.
local claim_ids = {}
for index, candidate in ipairs(claimable) do
  claim_ids[index] = candidate.mailbox_id .. ':' .. candidate.version
end
local records = chunked_hmget(KEYS[13], claim_ids, 1, #claim_ids)
for index, candidate in ipairs(claimable) do
  if not records[index] then
    return {'PROTOCOL', 'record_missing'}
  end
  candidate.record = records[index]
end

for _, mailbox_id in ipairs(removals) do
  redis.call('ZREM', KEYS[6], mailbox_id)
  redis.call('HDEL', KEYS[9], mailbox_id)
end
for _, rescore in ipairs(rescores) do
  redis.call('ZADD', KEYS[6], rescore[2], rescore[1])
end

local results = {}
for index, candidate in ipairs(claimable) do
  local request_token = ARGV[8 + index]
  local mailbox_id = candidate.mailbox_id
  local old_lease = candidate.old_lease
  if old_lease then
    delete_matching_poll_start(mailbox_id, old_lease.token)
    local old_receipt = candidate.old_receipt
    if old_receipt then
      redis.call('HDEL', KEYS[8], old_lease.token)
    end
    delete_capture(old_lease.token)
  end
  local deadline = now + lease_ttl
  local lease_value = request_token .. ':' .. tostring(deadline)
  local receipt_value = mailbox_id .. ':' .. request_token .. ':' .. tostring(deadline)
  redis.call('HSET', KEYS[7], mailbox_id, lease_value)
  redis.call('HSET', KEYS[8], request_token, receipt_value)
  redis.call('HSET', KEYS[9], mailbox_id, request_token .. ':' .. process_id)
  redis.call('HSET', KEYS[10], mailbox_id, request_token .. ':' .. tostring(now))
  redis.call('HSET', KEYS[14], request_token, candidate.record)
  redis.call('ZADD', KEYS[6], deadline, mailbox_id)
  results[index] = {
    'CLAIMED',
    request_token,
    mailbox_id,
    tostring(deadline),
    candidate.record
  }
end

if #claimable > 0 then
  return resolved_response(now, batch_size, results)
end
if #candidates > 0 and not saw_active then
  return {'RETRY_IMMEDIATELY', tostring(now)}
end
local earliest = earliest_due()
if earliest ~= '' and tonumber(earliest) <= now then
  return {'RETRY_IMMEDIATELY', tostring(now)}
end
return {'NOT_DUE', tostring(now), earliest}
