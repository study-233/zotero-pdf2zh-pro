// Keep browser reports in unique, managed directories; Python owns retention.
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

function invoke(arguments_) {
    const root = path.resolve(__dirname, '..');
    const candidates = [process.env.PDF2ZH_WORKSPACE_PYTHON,
        path.join(root, '.local-dev/runtime/venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'),
        process.platform === 'win32' ? 'python' : 'python3'].filter(Boolean);
    for (const python of candidates) {
        const result = spawnSync(python, ['-B', '-X', 'utf8', path.join(__dirname, 'workspace.py'),
            ...arguments_], {encoding: 'utf8', windowsHide: true});
        if (result.error?.code === 'ENOENT') continue;
        if (result.error || result.status !== 0) throw new Error(result.error?.message || result.stderr);
        return JSON.parse(result.stdout);
    }
    throw new Error('Python 3.12+ is required for managed development outputs.');
}

function newReport(kind) {
    const directory = invoke(['report', kind, '--owner-pid', String(process.pid)]).report;
    process.once('exit', code => {
        const marker = path.join(directory, '.workspace.json');
        const metadata = JSON.parse(fs.readFileSync(marker, 'utf8'));
        Object.assign(metadata, {owner_pid: null, status: code ? 'failed' : 'passed', finished: Date.now() / 1000});
        fs.writeFileSync(marker, JSON.stringify(metadata, null, 2) + '\n');
    });
    console.log('Validation evidence: ' + directory);
    return directory;
}

function newRun(kind) {
    const run = invoke(['start', kind, '--owner-pid', String(process.pid)]);
    process.once('exit', code => {
        try {
            invoke(['finish', run.work, '--status', code ? 'failed' : 'passed']);
        } catch (error) {
            console.error('Temporary workspace retained at ' + run.work + ': ' + error.message);
        }
    });
    console.log('Validation evidence: ' + run.report);
    return run;
}

module.exports = {newReport, newRun};
