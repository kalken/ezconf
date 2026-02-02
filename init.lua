-- Bootstrap Lazy.nvim
local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not vim.loop.fs_stat(lazypath) then
  vim.fn.system({
    "git",
    "clone",
    "--filter=blob:none",
    "https://github.com/folke/lazy.nvim.git",
    "--branch=stable", -- latest stable release
    lazypath,
  })
end
vim.opt.rtp:prepend(lazypath)

-- Silence the fake "deprecated" warning
vim.g.lspconfig_no_deprecate_warning = true
-- Use the new loader (eliminates warning cleanly)
vim.g.lspconfig_use_vim_loader = true

-- Set leader key before loading plugins
vim.g.mapleader = " "
vim.g.maplocalleader = " "

-- Core Neovim settings
-- vim.opt.number = true          -- Show line numbers
vim.opt.tabstop = 2            -- 2 spaces for tabs
vim.opt.shiftwidth = 2         -- 2 spaces for indentation
vim.opt.expandtab = true       -- Use spaces instead of tabs
vim.opt.smartindent = true     -- Smart indentation
vim.opt.termguicolors = true   -- Enable 24-bit RGB colors
vim.opt.guicursor = ""
vim.cmd('colorscheme industry')
vim.cmd('set mouse=')

-- Require your modules
require("window_focus_cycle")
local sidebar = require("heading_sidebar")
local button_panel = require("button_panel")

require("lazy").setup({
  {"neovim/nvim-lspconfig", dependencies = {"hrsh7th/cmp-nvim-lsp"}},
  {"hrsh7th/nvim-cmp", dependencies = {"hrsh7th/cmp-nvim-lsp", "hrsh7th/cmp-buffer"}},
}, {install = {colorscheme = { "default" }}})

--------------------------------------------------------------------
-- LSP + Autocomplete (automatic trigger enabled)
--------------------------------------------------------------------
local cmp = require'cmp'
cmp.setup({
  -- Enable automatic pop-ups
  completion = {
    autocomplete = { 'TextChanged', 'InsertEnter' },
  },

  sources = {
    { name = 'nvim_lsp' },
    { name = 'buffer' },
  },

  mapping = cmp.mapping.preset.insert({
    -- Ctrl+Space = trigger completion (or select next if already open)
    ['<C-Space>'] = cmp.mapping(function(fallback)
      if cmp.visible() then
        cmp.select_next_item()
      else
        cmp.complete()
      end
    end, { 'i', 's' }),

    -- Enter = confirm selection
    ['<CR>'] = cmp.mapping.confirm({ select = true }),

    -- Tab / Shift-Tab = navigate only when menu is visible
    ['<Tab>'] = cmp.mapping(function(fallback)
      if cmp.visible() then
        cmp.select_next_item()
      else
        fallback()
      end
    end, { 'i', 's' }),

    ['<S-Tab>'] = cmp.mapping(function(fallback)
      if cmp.visible() then
        cmp.select_prev_item()
      else
        fallback()
      end
    end, { 'i', 's' }),
  }),
})

--------------------------------------------------------------------
-- Nix LSP Setup with Dynamic Hostname Resolution
--------------------------------------------------------------------
vim.api.nvim_create_autocmd("FileType", {
  pattern = "nix",
  callback = function()
    -- Get system hostname (strip newline)
    local hostname = vim.fn.system("hostname"):gsub("%s+", "")

    vim.lsp.start({
      name = "nixd",
      cmd = { "nixd" },
      root_dir = vim.fs.dirname(
        vim.fs.find({ "flake.nix", ".nixd.json", ".git" }, {
          upward = true,
          path = vim.api.nvim_buf_get_name(0)
        })[1]
      ),
      settings = {
        nixd = {
          nixpkgs = {
            expr = string.format(
              '(builtins.getFlake "/etc/nixos").nixosConfigurations.%s.pkgs',
              hostname
            ),
          },
          formatting = {
            command = { "alejandra" },
          },
          options = {
            nixos = {
              expr = string.format(
                '(builtins.getFlake "/etc/nixos").nixosConfigurations.%s.options',
                hostname
              ),
            },
          },
        },
      },
      capabilities = vim.lsp.protocol.make_client_capabilities(),
      on_attach = function(client, bufnr)
        local opts = { buffer = bufnr, noremap = true, silent = true }
        vim.keymap.set('n', 'gd', vim.lsp.buf.definition, opts)
        vim.keymap.set('n', 'K',  vim.lsp.buf.hover, opts)
        vim.keymap.set('n', '<leader>rn', vim.lsp.buf.rename, opts)
        vim.keymap.set('n', '<leader>ca', vim.lsp.buf.code_action, opts)

        -- Optional: auto-format on save
        if client.supports_method("textDocument/formatting") then
          vim.api.nvim_create_autocmd("BufWritePre", {
            buffer = bufnr,
            callback = function()
              vim.lsp.buf.format({ bufnr = bufnr })
            end,
          })
        end
      end,
    })
  end,
})
