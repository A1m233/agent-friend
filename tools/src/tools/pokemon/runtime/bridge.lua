-- Original AF adapter for mGBA 0.10.5. No game RAM, cheats, or walkthroughs.
-- The launcher prepends AF_ROOT and AF_ROM_CRC32. Requests contain data only.
local root = assert(AF_ROOT)
local expectedCRC = assert(AF_ROM_CRC32)
local active = nil
local boot = tostring(os.time()) .. "_" .. tostring(math.random(100000, 999999))

local function read(path)
    local f = io.open(path, "rb")
    if not f then return nil end
    local data = f:read(8193)
    f:close()
    return data
end

local function exists(path)
    local f = io.open(path, "rb")
    if not f then return false end
    f:close()
    return true
end

local function write(path, data)
    local tmp = path .. ".tmp"
    local f = assert(io.open(tmp, "wb"))
    assert(f:write(data))
    assert(f:close())
    assert(os.rename(tmp, path))
end

local function validROM()
    local raw = emu:checksum()
    if type(raw) ~= "string" or #raw ~= 4 then return false end
    local hex = raw:gsub(".", function(c) return string.format("%02x", string.byte(c)) end)
    return hex == expectedCRC
end

local function finish(status, code)
    emu:setKeys(0)
    local a = active
    active = nil
    if not a then return end
    if status == "completed" then
        local ok = pcall(function()
            if a.kind == "checkpoint" then
                assert(emu:saveStateFile(root .. "/saves/" .. a.id .. ".ss0"))
            end
            emu:screenshot(root .. "/screens/" .. a.id .. ".png")
        end)
        if not ok then status, code = "failed", "capture_failed" end
    end
    write(root .. "/receipts/" .. a.id .. ".txt",
          a.id .. "|" .. status .. "|" .. a.elapsed .. "|" .. code .. "|" .. boot .. "\n")
end

local function parse(line)
    if #line > 8192 then return nil end
    local id, kind, deadline, wire = line:match("^1|([a-f0-9]+)|([a-z]+)|(%d+)|([^\r\n]+)\n?$")
    if not id or #id ~= 32 then return nil end
    local a = {id=id, kind=kind, deadline=tonumber(deadline), steps={}, index=1, elapsed=0}
    if kind ~= "act" and kind ~= "observe" and kind ~= "checkpoint" and kind ~= "restore" then return a, "invalid_kind" end
    if kind == "act" then
        local total, rebuilt = 0, {}
        for piece in wire:gmatch("[^,]+") do
            local mask, frames = piece:match("^(%d+):(%d+)$")
            mask, frames = tonumber(mask), tonumber(frames)
            if not mask or not frames or mask > 1023 or frames < 1 or frames > 600 then
                return a, "invalid_steps"
            end
            if (mask & 48) == 48 or (mask & 192) == 192 then return a, "conflicting_keys" end
            total = total + frames
            table.insert(a.steps, {mask=mask, frames=frames})
            table.insert(rebuilt, piece)
        end
        if #a.steps < 1 or #a.steps > 32 or total > 600 or table.concat(rebuilt, ",") ~= wire then
            return a, "frame_limit"
        end
    elseif kind == "restore" then
        if #wire ~= 32 or not wire:match("^[a-f0-9]+$") then return a, "invalid_restore" end
        a.restore = wire
    elseif wire ~= "-" then return a, "invalid_payload" end
    return a, nil
end

local function tick()
    if active then
        if exists(root .. "/cancel") or exists(root .. "/paused") or os.time() >= active.deadline then
            finish("cancelled", "cancelled_or_expired")
            return
        end
        active.elapsed = active.elapsed + 1
        active.remaining = active.remaining - 1
        if active.remaining == 0 then
            active.index = active.index + 1
            local step = active.steps[active.index]
            if not step then finish("completed", "ok"); return end
            active.remaining = step.frames
            emu:setKeys(step.mask)
        end
        return
    end
    local path = root .. "/request.txt"
    local raw = read(path)
    if not raw then return end
    local a, err = parse(raw)
    if not a then
        emu:setKeys(0)
        console:error("AF: invalid request; preserving file for diagnosis")
        return
    end
    -- Keep the request until a receipt exists. After restart, expired/cancelled requests
    -- cannot run again. The host never resends an unknown operation.
    if exists(root .. "/receipts/" .. a.id .. ".txt") then
        os.remove(path)
        return
    end
    active = a
    if err then finish("failed", err); return end
    if not validROM() then finish("failed", "rom_mismatch"); return end
    if os.time() >= a.deadline then finish("cancelled", "expired"); return end
    if a.kind == "observe" then finish("completed", "ok"); return end
    if a.kind == "restore" then
        if not exists(root .. "/paused") then finish("failed", "pause_required"); return end
        local claim = root .. "/claims/" .. a.id
        if exists(claim) then finish("failed", "interrupted_operation"); return end
        write(claim, boot .. "\n")
        if not emu:loadStateFile(root .. "/saves/" .. a.restore .. ".ss0", C.SAVESTATE.ALL) then
            finish("failed", "restore_failed"); return
        end
        finish("completed", "ok")
        return
    end
    if a.kind == "checkpoint" then finish("completed", "ok"); return end
    if exists(root .. "/paused") or exists(root .. "/cancel") then
        finish("cancelled", "paused"); return
    end
    -- Claim before the first key, durably. A second script instance or restart must
    -- not execute a partly completed operation again.
    local claim = root .. "/claims/" .. a.id
    if exists(claim) then finish("failed", "interrupted_operation"); return end
    write(claim, boot .. "\n")
    active.remaining = active.steps[1].frames
    emu:setKeys(active.steps[1].mask)
end

emu:setKeys(0)
callbacks:add("frame", function()
    local ok, err = pcall(tick)
    if not ok then
        emu:setKeys(0)
        console:error("AF adapter stopped: " .. tostring(err))
        active = nil
        -- Host pending journal remains unresolved; never retry the action here.
        write(root .. "/paused", "adapter_error\n")
    end
end)
console:log("AF visual bridge ready: " .. boot)
