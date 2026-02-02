local api = vim.api
local fn = vim.fn

local sidebar_buf, sidebar_win, main_buf, main_win

local function collect_headings(buf)
  if not buf or not api.nvim_buf_is_loaded(buf) then return {} end
  local lines = api.nvim_buf_get_lines(buf, 0, -1, false)
  local headings = {}
  for i, line in ipairs(lines) do
    local level, title = line:match("^%s*(%#+)!%s+(.-)%s*$")
    if level and title ~= "" then
      table.insert(headings, {level = #level, title = title, line = i})
    end
  end
  return headings
end

local function update_border_highlight(focused)
  -- Sidebar appearance doesn't change, this function exists for compatibility
end

local function render_headings()
  if not sidebar_buf or not api.nvim_buf_is_valid(sidebar_buf) then return end
  local headings = collect_headings(main_buf)
  local sidebar_lines = {}

  for _, h in ipairs(headings) do
    local indent = string.rep("  ", h.level - 1)
    table.insert(sidebar_lines, " " .. indent .. h.title)
  end

  api.nvim_buf_set_option(sidebar_buf, "modifiable", true)
  api.nvim_buf_set_lines(sidebar_buf, 0, -1, false, sidebar_lines)
  api.nvim_buf_set_option(sidebar_buf, "modifiable", false)

  -- Use theme colors for headings
  for i, h in ipairs(headings) do
    local hl_group
    if h.level == 1 then
      hl_group = "Comment"
    elseif h.level == 2 then
      hl_group = "Normal"
    elseif h.level == 3 then
      hl_group = "Normal"
    else
      hl_group = "String"
    end
    api.nvim_buf_add_highlight(sidebar_buf, -1, hl_group, i - 1, 0, -1)
  end
end

local function close_sidebar()
  if sidebar_win and api.nvim_win_is_valid(sidebar_win) then
    api.nvim_win_close(sidebar_win, true)
  end
  if sidebar_buf and api.nvim_buf_is_valid(sidebar_buf) then
    api.nvim_buf_delete(sidebar_buf, {force = true})
  end
  sidebar_buf, sidebar_win, main_buf, main_win = nil, nil, nil, nil
  -- print("Sidebar closed")
end

local function focus_main_editor()
  if main_win and api.nvim_win_is_valid(main_win) then
    api.nvim_set_current_win(main_win)
    update_border_highlight(false)
  end
end

local function open_sidebar()
  local ext = fn.expand("%:e")
  if ext ~= "nix" then
    print("Sidebar only works on .nix files")
    return
  end

  main_buf = api.nvim_get_current_buf()
  main_win = api.nvim_get_current_win()

  sidebar_buf = api.nvim_create_buf(false, true)
  api.nvim_buf_set_option(sidebar_buf, "bufhidden", "wipe")
  api.nvim_buf_set_option(sidebar_buf, "filetype", "markdown")
  api.nvim_buf_set_option(sidebar_buf, "modifiable", false)

  local width = 30
  local height = vim.o.lines - 2
  local row = 0
  local col = vim.o.columns - width - 1

  sidebar_win = api.nvim_open_win(sidebar_buf, false, {
    relative = "editor",
    width = width,
    height = height,
    row = row,
    col = col,
    style = "minimal",
  })

  api.nvim_win_set_option(sidebar_win, "number", false)
  api.nvim_win_set_option(sidebar_win, "relativenumber", false)
  api.nvim_win_set_option(sidebar_win, "signcolumn", "no")

  -- Match sidebar background to Normal highlight background
  local normal_hl = api.nvim_get_hl(0, {name = "Normal"})
  local bg_color = normal_hl.bg and string.format("#%06x", normal_hl.bg) or nil

  if bg_color then
    api.nvim_command("highlight SidebarNormal guibg=" .. bg_color .. " ctermbg=NONE")
  else
    api.nvim_command("highlight SidebarNormal guibg=NONE ctermbg=NONE")
  end

  -- Create focused border highlight
  api.nvim_command("highlight SidebarFocused guifg=#ffff00 ctermfg=yellow")

  -- Create focused border highlight
  api.nvim_command("highlight SidebarFocused guifg=#ffff00 ctermfg=yellow")

  api.nvim_win_set_option(sidebar_win, "winhl", "Normal:SidebarNormal")

  render_headings()

  api.nvim_create_autocmd({"TextChanged", "TextChangedI", "BufEnter", "BufWinEnter"}, {
    buffer = main_buf,
    callback = render_headings,
  })

  -- Update border highlight when entering/leaving sidebar
  api.nvim_create_autocmd("WinEnter", {
    buffer = sidebar_buf,
    callback = function()
      update_border_highlight(true)
    end,
  })

  api.nvim_create_autocmd("WinLeave", {
    buffer = sidebar_buf,
    callback = function()
      update_border_highlight(false)
    end,
  })

  -- Update border highlight when entering/leaving sidebar
  api.nvim_create_autocmd("WinEnter", {
    buffer = sidebar_buf,
    callback = function()
      update_border_highlight(true)
    end,
  })

  api.nvim_create_autocmd("WinLeave", {
    buffer = sidebar_buf,
    callback = function()
      update_border_highlight(false)
    end,
  })

  api.nvim_buf_set_keymap(sidebar_buf, "n", "<CR>", "", {
    noremap = true, silent = true,
    callback = function()
      local line = api.nvim_win_get_cursor(sidebar_win)[1]
      local headings = collect_headings(main_buf)
      if line > 0 and line <= #headings then
        local target = headings[line].line
        if main_win and api.nvim_win_is_valid(main_win) then
          api.nvim_set_current_win(main_win)
          api.nvim_win_set_cursor(main_win, {target, 0})
          api.nvim_win_call(main_win, function() vim.cmd("normal! zt") end)
          -- Close sidebar after jumping
          close_sidebar()
        end
      end
    end,
  })

  -- Escape returns to editor instead of closing
  api.nvim_buf_set_keymap(sidebar_buf, "n", "<Esc>", "", {
    noremap = true, silent = true,
    callback = focus_main_editor,
  })

  api.nvim_create_user_command("CloseSidebar", close_sidebar, {})
end

local function toggle_sidebar()
  if sidebar_win and api.nvim_win_is_valid(sidebar_win) then
    close_sidebar()
  else
    open_sidebar()
  end
end

api.nvim_create_user_command("HeadingSidebarToggle", toggle_sidebar, {})

return {
  toggle = toggle_sidebar,
  open = open_sidebar,
  close = close_sidebar,
  get_windows = function()
    return sidebar_win, main_win
  end,
  update_border_highlight = update_border_highlight,
}
