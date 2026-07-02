#!/usr/bin/env node

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const packageRoot = path.resolve(__dirname, '..');
const harnessRoot = path.join(packageRoot, '.harness');
const venvDir = path.join(harnessRoot, '.venv');
const venvPython = process.platform === 'win32'
  ? path.join(venvDir, 'Scripts', 'python.exe')
  : path.join(venvDir, 'bin', 'python3');
const requirements = path.join(harnessRoot, 'requirements.txt');

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: packageRoot,
    stdio: 'inherit',
    ...options,
  });

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed with exit code ${result.status}`);
  }
}

function checkPythonVersion(command) {
  const result = spawnSync(command, ['-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'], {
    cwd: packageRoot,
    encoding: 'utf8',
  });

  if (result.error || result.status !== 0) {
    return false;
  }

  const [major, minor] = result.stdout.trim().split('.').map(Number);
  return major > 3 || (major === 3 && minor >= 9);
}

function findPython() {
  const candidates = process.platform === 'win32' ? ['python'] : ['python3', 'python'];
  return candidates.find((candidate) => checkPythonVersion(candidate));
}

function main() {
  if (fs.existsSync(venvPython)) {
    process.exit(0);
  }

  const python = findPython();
  if (!python) {
    console.error('harness postinstall: 未找到 Python 3.9+，请先安装 python3 后重新运行 npm install。');
    process.exit(1);
  }

  if (!fs.existsSync(requirements)) {
    console.error(`harness postinstall: requirements.txt 不存在: ${requirements}`);
    process.exit(1);
  }

  try {
    run(python, ['-m', 'venv', venvDir]);
    run(venvPython, ['-m', 'pip', 'install', '-r', requirements]);
  } catch (error) {
    console.error(`harness postinstall: Python 环境初始化失败：${error.message}`);
    process.exit(1);
  }
}

main();
