-- Construct complete downstream AutoScribe call records for dispatch.
--
-- Runtime contract supplied by the ephemeral defaults file:
--   input-files: [absolute source path]
--   metadata:
--     plan: <dispatch plan slug>

local stringify = pandoc.utils.stringify

local function trim(text)
  return tostring(text or ""):match("^%s*(.-)%s*$")
end

local function fail(message)
  error("autoscribe dispatch: " .. message, 0)
end

local function pandoc_type(value)
  if value == nil then return nil end
  if pandoc.utils and pandoc.utils.type then
    local ok, result = pcall(pandoc.utils.type, value)
    if ok then return result end
  end
  return type(value)
end

local function is_array_table(value)
  if type(value) ~= "table" then return false end
  local count = 0
  for key, _ in pairs(value) do
    if type(key) ~= "number" then return false end
    count = count + 1
  end
  return count > 0
end

local function meta_to_plain(value)
  if value == nil then return nil end
  local kind = pandoc_type(value)
  if kind == "MetaMap" then
    local out = {}
    for key, item in pairs(value) do out[key] = meta_to_plain(item) end
    return out
  end
  if kind == "MetaList" or kind == "List" then
    local out = {}
    for _, item in ipairs(value) do out[#out + 1] = meta_to_plain(item) end
    return out
  end
  if kind == "MetaBool" then
    if type(value) == "boolean" then return value end
    if type(value) == "table" and value.c ~= nil then return value.c end
    return value
  end
  if kind == "MetaString" or kind == "MetaInlines" or kind == "Inlines" then
    return stringify(value)
  end
  if kind == "MetaBlocks" or kind == "Blocks" then
    return trim(pandoc.write(pandoc.Pandoc(value, pandoc.Meta({})), "markdown"))
  end
  local lua_type = type(value)
  if lua_type == "string" or lua_type == "number" or lua_type == "boolean" then return value end
  if lua_type ~= "table" then return tostring(value) end
  local out = {}
  if is_array_table(value) then
    for i = 1, #value do out[i] = meta_to_plain(value[i]) end
  else
    for key, item in pairs(value) do out[key] = meta_to_plain(item) end
  end
  return out
end

local function first_non_blank(...)
  for index = 1, select("#", ...) do
    local value = select(index, ...)
    local plain = meta_to_plain(value)
    if (type(plain) == "string" or type(plain) == "number") and trim(plain) ~= "" then
      return trim(plain)
    end
  end
  return nil
end

local function markdown_content(blocks)
  return trim(pandoc.write(pandoc.Pandoc(blocks, pandoc.Meta({})), "markdown"))
end

local function has_class(block, class_name)
  if block.t ~= "Div" then return false end
  for _, class in ipairs(block.classes or {}) do
    if tostring(class):lower() == class_name then return true end
  end
  return false
end

local function extract_leading_directive(blocks)
  local first = blocks[1]
  if first == nil or not has_class(first, "directive") then return nil end
  local directive = markdown_content(first.content)
  blocks:remove(1)
  if directive == "" then fail("leading directive is blank") end
  return directive
end

local function input_path()
  local inputs = PANDOC_STATE and PANDOC_STATE.input_files or {}
  if #inputs ~= 1 then fail("exactly one input file is required") end
  local value = tostring(inputs[1] or "")
  if trim(value) == "" then fail("input filepath is missing") end
  return value:gsub("\\", "/")
end

local function filename(path)
  return path:match("([^/]+)$") or path
end

local function logical_source_path(path)
  -- Service dispatch reads from a detached temporary worktree. Preserve the
  -- established repository-relative source_path value in the call contract.
  local marker = "/worktree/"
  local start = path:find(marker, 1, true)
  if start ~= nil then return path:sub(start + #marker) end
  return path
end

function Pandoc(doc)
  local metadata = {}
  local legacy_identity = nil
  for key, value in pairs(doc.meta or {}) do
    local text_key = tostring(key)
    local plain_value = meta_to_plain(value)
    if text_key == "identity" then
      legacy_identity = plain_value
    else
      metadata[text_key] = plain_value
    end
  end

  local identity = first_non_blank(
    metadata.record_identity,
    metadata.identifier,
    metadata.slug,
    legacy_identity
  )
  if identity == nil then fail("document slug is missing") end
  local plan = first_non_blank(metadata.plan)
  if plan == nil then fail("plan metadata is missing") end

  local extra_metadata = {}
  for key, value in pairs(metadata) do
    if key ~= "record_type" and key ~= "record_identity" and key ~= "record_plan"
        and key ~= "plan" then
      extra_metadata[key] = value
    end
  end
  extra_metadata.identity = nil

  local directive = extract_leading_directive(doc.blocks)
  local content = markdown_content(doc.blocks)
  if content == "" then fail(identity .. ": document content is blank") end
  local source_path = input_path()
  local call = {
    type = "call",
    identity = identity,
    content = content,
    plan = plan,
    extra = {
      filename_hint = filename(source_path),
      source_path = logical_source_path(source_path),
      metadata = extra_metadata,
    },
  }
  if directive ~= nil then call.directive = directive end

  io.stdout:write(pandoc.json.encode(call) .. "\n")
  return pandoc.Pandoc({}, pandoc.Meta({}))
end
