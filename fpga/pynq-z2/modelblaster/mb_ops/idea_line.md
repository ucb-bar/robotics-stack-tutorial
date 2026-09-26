### One more output rule: say what this version tries

Right after the `#include` lines, the ```c block must have exactly one comment line of the form

    // idea: <what this version does differently, in under 70 characters>

for example `// idea: MAX8 on 8 columns of two rows at once, scalar fallback`. It is shown to the
people watching the optimization. It does not change what the code must do.
