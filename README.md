# Ezconf
This is a small configuration wrapped in nvim to make it easier to edit nix files.



## Side panel
Looks through the file and checks for comments starting with #!. 

```
#! Heading 1
##! Heading 2
###! Heading 3
####! Heading 2
```

This indicates a headline that should be presented. The number of # decides the headline level, just like markdown. Press Enter on a selected headline to go there.

## Button bar
Looks through the file and checks for comments starting with #!button. This indicates that a button should be created. the syntax of buttons is: 

```
#!button List Generations: nixos-rebuild list-generations
#!button Rebuild System: nixos-rebuild switch
#!button Kernel Version: uname -a
```

Display a button bar where you can choose to run commands. the syntax. Press Enter on a selected option to run the command specified.

## Navigation
Switch between Side panel, Button Bar and Main window by pressing TAB (while not in edit mode).


## Included tools:
### Nixd
Automatic configuration of nixd (lsp) autocomplete functionality.

### alejandra
Automatic formatting on save.
