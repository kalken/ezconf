local api = vim.api

local sidebar = require("heading_sidebar")
local button_panel = require("button_panel")

local function has_headings()
  local main_buf = api.nvim_get_current_buf()
  if not main_buf or not api.nvim_buf_is_loaded(main_buf) then return false end
  local lines = api.nvim_buf_get_lines(main_buf, 0, -1, false)
  for _, line in ipairs(lines) do
    local level, title = line:match("^%s*(%#+)!%s+(.-)%s*$")
    if level and title ~= "" then
      return true
    end
  end
  return false
end

local function has_buttons()
  local main_buf = api.nvim_get_current_buf()
  if not main_buf or not api.nvim_buf_is_loaded(main_buf) then return false end
  local lines = api.nvim_buf_get_lines(main_buf, 0, -1, false)
  for _, line in ipairs(lines) do
    local name, cmd = line:match("^#!button%s+([^:]+):%s*(.+)$")
    if name and cmd then
      return true
    end
  end
  return false
end

local function cycle_focus()
  local sidebar_win, sidebar_main_win = sidebar.get_windows()
  local button_win, button_main_win = button_panel.get_windows()
  local main_win = sidebar_main_win or button_main_win or api.nvim_get_current_win()
  
  local current_win = api.nvim_get_current_win()
  
  -- If we're in the main editor
  if current_win == main_win then
    -- If nothing is open, check if we have headings to open sidebar
    if not (sidebar_win and api.nvim_win_is_valid(sidebar_win)) and not (button_win and api.nvim_win_is_valid(button_win)) then
      if has_headings() then
        sidebar.open()
        vim.schedule(function()
          local new_sidebar_win = sidebar.get_windows()
          if new_sidebar_win and api.nvim_win_is_valid(new_sidebar_win) then
            api.nvim_set_current_win(new_sidebar_win)
          end
        end)
      elseif has_buttons() then
        button_panel.open()
        vim.schedule(function()
          local new_button_win = button_panel.get_windows()
          if new_button_win and api.nvim_win_is_valid(new_button_win) then
            api.nvim_set_current_win(new_button_win)
            button_panel.update_panel_highlight(true)
          end
        end)
      end
    -- If sidebar is open, switch to it
    elseif sidebar_win and api.nvim_win_is_valid(sidebar_win) then
      api.nvim_set_current_win(sidebar_win)
    -- If only button panel is open, switch to it
    elseif button_win and api.nvim_win_is_valid(button_win) then
      api.nvim_set_current_win(button_win)
    end
    return
  end
  
  -- If we're in sidebar, close it, open button panel if there are buttons
  if current_win == sidebar_win then
    sidebar.close()
    if has_buttons() then
      button_panel.open()
      vim.schedule(function()
        local new_button_win = button_panel.get_windows()
        if new_button_win and api.nvim_win_is_valid(new_button_win) then
          api.nvim_set_current_win(new_button_win)
          button_panel.update_panel_highlight(true)
        end
      end)
    else
      vim.schedule(function()
        if main_win and api.nvim_win_is_valid(main_win) then
          api.nvim_set_current_win(main_win)
        end
      end)
    end
    return
  end
  
  -- If we're in button panel, close it and return to editor
  if current_win == button_win then
    button_panel.close()
    vim.schedule(function()
      local updated_main_win = button_panel.get_windows() or main_win
      if main_win and api.nvim_win_is_valid(main_win) then
        api.nvim_set_current_win(main_win)
      end
    end)
    return
  end
end

api.nvim_set_keymap("n", "<Tab>", "", {
  noremap = true,
  silent = true,
  callback = cycle_focus,
})

return {
  cycle_focus = cycle_focus,
}
