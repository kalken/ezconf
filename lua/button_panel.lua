local api = vim.api
local fn = vim.fn

local button_buf, button_win, main_buf, main_win
local terminal_win, terminal_buf
local current_button_index = 1
local ns = api.nvim_create_namespace("button_panel")

local function collect_buttons()
  if not main_buf or not api.nvim_buf_is_loaded(main_buf) then return {} end
  local lines = api.nvim_buf_get_lines(main_buf, 0, -1, false)
  local buttons = {}
  for _, line in ipairs(lines) do
    local name, cmd = line:match("^#!button%s+([^:]+):%s*(.+)$")
    if name and cmd then
      table.insert(buttons, {name = name, cmd = cmd})
    end
  end
  return buttons
end

local function update_panel_highlight(focused)
  -- Button panel appearance doesn't change, this function exists for compatibility
end

local function render_buttons()
  if not button_buf or not api.nvim_buf_is_valid(button_buf) then return end
  local buttons = collect_buttons()
  local line = ""

  for i, btn in ipairs(buttons) do
    if i == current_button_index then
      line = line .. string.format(" [%s] ", btn.name)
    else
      line = line .. string.format("  %s  ", btn.name)
    end
  end

  api.nvim_buf_set_option(button_buf, "modifiable", true)
  api.nvim_buf_set_lines(button_buf, 0, -1, false, {line})
  api.nvim_buf_set_option(button_buf, "modifiable", false)

  -- Clear previous highlights
  api.nvim_buf_clear_namespace(button_buf, ns, 0, -1)

  -- Add highlights for each button block
  local current_col = 0
  for i, btn in ipairs(buttons) do
    local fmt = i == current_button_index and " [%s] " or "  %s  "
    local block = string.format(fmt, btn.name)
    local block_len = #block
    local hl_group = i == current_button_index and 'Comment' or 'Normal'
    api.nvim_buf_add_highlight(button_buf, ns, hl_group, 0, current_col, current_col + block_len)
    current_col = current_col + block_len
  end
end

local function close_button_panel()
  if button_win and api.nvim_win_is_valid(button_win) then
    api.nvim_win_close(button_win, true)
  end
  if button_buf and api.nvim_buf_is_valid(button_buf) then
    api.nvim_buf_delete(button_buf, {force = true})
  end
  if terminal_win and api.nvim_win_is_valid(terminal_win) then
    api.nvim_win_close(terminal_win, true)
  end
  button_buf, button_win, terminal_buf, terminal_win, main_buf, main_win = nil, nil, nil, nil, nil, nil
  -- print("Button panel closed")
end

local function focus_main_editor()
  if main_win and api.nvim_win_is_valid(main_win) then
    api.nvim_set_current_win(main_win)
    update_panel_highlight(false)
  end
end

local function open_button_panel()
  -- Get the main buffer from the editor window, not current buffer
  local sidebar = require("heading_sidebar")
  local _, sidebar_main_win = sidebar.get_windows()
  
  if sidebar_main_win and api.nvim_win_is_valid(sidebar_main_win) then
    main_win = sidebar_main_win
    main_buf = api.nvim_win_get_buf(main_win)
  else
    main_win = api.nvim_get_current_win()
    main_buf = api.nvim_get_current_buf()
  end
  
  -- Check if the main buffer is a .nix file
  local bufname = api.nvim_buf_get_name(main_buf)
  local ext = bufname:match("^.+%.(.+)$")
  if ext ~= "nix" then
    print("Button panel only works on .nix files")
    return
  end

  button_buf = api.nvim_create_buf(false, true)
  api.nvim_buf_set_option(button_buf, "bufhidden", "wipe")
  api.nvim_buf_set_option(button_buf, "modifiable", false)
  api.nvim_buf_set_option(button_buf, "filetype", "markdown")

  local width = vim.o.columns
  local height = 1
  local row = vim.o.lines - 3
  local col = 0

  button_win = api.nvim_open_win(button_buf, false, {
    relative = "editor",
    width = width,
    height = height,
    row = row,
    col = col,
    style = "minimal",
    border = "none",
  })

  api.nvim_win_set_option(button_win, "winhl", "Normal:Normal")
  api.nvim_win_set_option(button_win, "cursorline", false)
  api.nvim_win_set_option(button_win, "number", false)
  api.nvim_win_set_option(button_win, "relativenumber", false)
  api.nvim_win_set_option(button_win, "signcolumn", "no")
  api.nvim_win_set_option(button_win, "wrap", false)

  render_buttons()

  api.nvim_buf_set_keymap(button_buf, "n", "<CR>", "", {
    noremap = true, silent = true,
    callback = function()
      local buttons = collect_buttons()
      local btn = buttons[current_button_index]
      if btn then
        -- Create output buffer
        local output_buf = api.nvim_create_buf(false, true)
        api.nvim_buf_set_option(output_buf, "buftype", "nofile")
        api.nvim_buf_set_option(output_buf, "bufhidden", "wipe")
        api.nvim_buf_set_option(output_buf, "modifiable", true)
        
        -- Add command line without extra blank line
        api.nvim_buf_set_lines(output_buf, 0, -1, false, {"$ " .. btn.cmd})
        
        -- Open split window and keep focus on it
        vim.cmd("split")
        local output_win = api.nvim_get_current_win()
        api.nvim_win_set_buf(output_win, output_buf)
        
        local line_count = 1
        local command_finished = false
        
        -- Map Enter to return to button panel when command is done
        api.nvim_buf_set_keymap(output_buf, "n", "<CR>", "", {
          noremap = true, silent = true,
          callback = function()
            if command_finished then
              -- Close output window and return to button panel
              if api.nvim_win_is_valid(output_win) then
                api.nvim_win_close(output_win, true)
              end
              if button_win and api.nvim_win_is_valid(button_win) then
                api.nvim_set_current_win(button_win)
              end
            end
          end,
        })
        
        -- Run command with streaming output
        vim.system({'sh', '-c', btn.cmd}, {
          stdout = function(err, data)
            if data then
              vim.schedule(function()
                local lines = vim.split(data, "\n", {plain = true})
                -- Filter out the last line if it's empty (from trailing newline)
                if #lines > 0 and lines[#lines] == "" then
                  table.remove(lines)
                end
                if #lines > 0 then
                  api.nvim_buf_set_lines(output_buf, line_count, line_count, false, lines)
                  line_count = line_count + #lines
                  -- Scroll to bottom
                  if api.nvim_win_is_valid(output_win) then
                    api.nvim_win_set_cursor(output_win, {line_count, 0})
                  end
                end
              end)
            end
          end,
          stderr = function(err, data)
            if data then
              vim.schedule(function()
                local lines = vim.split(data, "\n", {plain = true})
                -- Filter out the last line if it's empty (from trailing newline)
                if #lines > 0 and lines[#lines] == "" then
                  table.remove(lines)
                end
                if #lines > 0 then
                  api.nvim_buf_set_lines(output_buf, line_count, line_count, false, lines)
                  line_count = line_count + #lines
                  -- Scroll to bottom
                  if api.nvim_win_is_valid(output_win) then
                    api.nvim_win_set_cursor(output_win, {line_count, 0})
                  end
                end
              end)
            end
          end,
        }, function(result)
          vim.schedule(function()
            command_finished = true
            api.nvim_buf_set_option(output_buf, "modifiable", false)
          end)
        end)
      else
        print("No button selected")
      end
    end,
  })

  api.nvim_buf_set_keymap(button_buf, "n", "<Left>", "", {
    noremap = true, silent = true,
    callback = function()
      local buttons = collect_buttons()
      if #buttons > 0 then
        current_button_index = current_button_index - 1
        if current_button_index < 1 then
          current_button_index = #buttons
        end
        render_buttons()
      end
    end,
  })

  api.nvim_buf_set_keymap(button_buf, "n", "<Right>", "", {
    noremap = true, silent = true,
    callback = function()
      local buttons = collect_buttons()
      if #buttons > 0 then
        current_button_index = current_button_index + 1
        if current_button_index > #buttons then
          current_button_index = 1
        end
        render_buttons()
      end
    end,
  })

  api.nvim_buf_set_keymap(button_buf, "n", "<Esc>", "", {
    noremap = true, silent = true,
    callback = focus_main_editor,
  })

  api.nvim_create_autocmd({"TextChanged", "TextChangedI", "BufEnter"}, {
    buffer = main_buf,
    callback = render_buttons,
  })
end

local function toggle_button_panel()
  if button_win and api.nvim_win_is_valid(button_win) then
    close_button_panel()
  else
    open_button_panel()
  end
end

api.nvim_create_user_command("ButtonPanelToggle", toggle_button_panel, {})

return {
  toggle = toggle_button_panel,
  open = open_button_panel,
  close = close_button_panel,
  get_windows = function()
    return button_win, main_win
  end,
  update_panel_highlight = update_panel_highlight,
}
