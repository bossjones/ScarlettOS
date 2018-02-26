# VScode Command line

VS Code has a powerful command line interface (CLI) to help you customize the editor launch your specific scenarios.

```
# open code with current directory
code .

# open the current directory in the most recently used code window
code -r .

# create a new window
code -n

# change the language
code --locale=es

# open diff editor
code --diff <file1> <file2>

# open file at specific line and column <file:line[:character]>
code --goto package.json:10:5

# see help options
code --help

# disable all extensions
code --disable-extensions .
```

# VScode Variable reference

Taken directly from: https://github.com/Microsoft/vscode-docs/blob/master/docs/editor/variables-reference.md

Visual Studio Code supports variable substitution in [Debugging](/docs/editor/debugging.md) and [Task](/docs/editor/tasks.md) configuration files. Variable substitution is supported inside strings in `launch.json` and `tasks.json` files using **${variableName}** syntax.

## Predefined variables

The following predefined variables are supported:

- **${workspaceFolder}** - the path of the folder opened in VS Code
- **${workspaceFolderBasename}** - the name of the folder opened in VS Code without any slashes (/)
- **${file}** - the current opened file
- **${relativeFile}** - the current opened file relative to `workspaceFolder`
- **${fileBasename}** - the current opened file's basename
- **${fileBasenameNoExtension}** - the current opened file's basename with no file extension
- **${fileDirname}** - the current opened file's dirname
- **${fileExtname}** - the current opened file's extension
- **${cwd}** - the task runner's current working directory on startup
- **${lineNumber}** - the current selected line number in the active file

**Note**: The `${workspaceRoot}` variable is deprecated in favor of the `${workspaceFolder}` variable.

## Environment variables

You can also reference environment variables through **${env:Name}** syntax (for example, `${env:PATH}`).

```json
{
    "type": "node",
    "request": "launch",
    "name": "Launch Program",
    "program": "${workspaceFolder}/app.js",
    "cwd": "${workspaceFolder}",
    "args": [ "${env:USERNAME}" ]
}
```

**Note**: Be sure to match the environment variable name's casing, for example `${env:Path}` on Windows.

## Settings and command variables

You can reference VS Code settings and commands using the following syntax:

* **${config:Name}** - example: `${config:editor.fontSize}`
* **${command:CommandID}** - example: `${command:explorer.newFolder}`

## Variables scoped per workspace folder

By appending the root folder's name to a variable (separated by a colon), it is possible to reach into sibling root folders of a workspace. Without the root folder name the variable is scoped to the same folder where it is used.

For example, in a multi root workspace with folders `Server` and `Client`, a `${workspaceFolder:Client}` refers to the path of the `Client` root.

## Common Questions

**Q: Is variable substitution supported in User and Workspace settings?**

**A:** No, the predefined variables are not supported in strings in `settings.json` files. Some [settings](/docs/getstarted/settings.md) like `window.title` have their own variables:

```json
  "window.title": "${dirty}${activeEditorShort}${separator}${rootName}${separator}${appName}"
```

Refer to the comments in the Settings editor (`kb(workbench.action.openGlobalSettings)`) to learn about setting specific variables.

**Q: Why isn't ${workspaceRoot} documented?**

**A:** The variable `${workspaceRoot}` was deprecated in favor of `${workspaceFolder}` to better align with [Multi-root Workspace](/docs/editor/multi-root-workspaces.md) support.
