import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import {
  cpSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync
} from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const nodeModules = join(repositoryRoot, 'node_modules');
const vendorRoot = join(repositoryRoot, 'static', 'vendor');

function packageMetadata(packageName) {
  return JSON.parse(
    readFileSync(join(nodeModules, packageName, 'package.json'), 'utf8')
  );
}

function copyFile(sourceRelative, destinationRelative) {
  const source = join(repositoryRoot, sourceRelative);
  const destination = join(vendorRoot, destinationRelative);
  mkdirSync(dirname(destination), { recursive: true });
  cpSync(source, destination);
}

function copyDirectory(sourceRelative, destinationRelative) {
  const source = join(repositoryRoot, sourceRelative);
  const destination = join(vendorRoot, destinationRelative);
  mkdirSync(dirname(destination), { recursive: true });
  cpSync(source, destination, { recursive: true });
}

function copyFirstExisting(candidates, destinationRelative) {
  for (const candidate of candidates) {
    const absolute = join(repositoryRoot, candidate);
    try {
      if (statSync(absolute).isFile()) {
        copyFile(candidate, destinationRelative);
        return;
      }
    } catch {
      // Continue to the next package-specific license filename.
    }
  }
  throw new Error(`No license file found for ${destinationRelative}`);
}

function collectFiles(directory) {
  return readdirSync(directory, { withFileTypes: true })
    .flatMap(entry => {
      const absolute = join(directory, entry.name);
      return entry.isDirectory() ? collectFiles(absolute) : [absolute];
    })
    .sort();
}

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

rmSync(vendorRoot, { recursive: true, force: true });
mkdirSync(join(vendorRoot, 'tailwind'), { recursive: true });

execFileSync(
  process.execPath,
  [
    join(nodeModules, 'tailwindcss', 'lib', 'cli.js'),
    '--config',
    join(repositoryRoot, 'tailwind.config.cjs'),
    '--input',
    join(repositoryRoot, 'static', 'src', 'tailwind.css'),
    '--output',
    join(vendorRoot, 'tailwind', 'tailwind.min.css'),
    '--minify'
  ],
  { cwd: repositoryRoot, stdio: 'inherit' }
);

copyFile('node_modules/jquery/dist/jquery.min.js', 'jquery/jquery.min.js');
copyFile('node_modules/toastr/build/toastr.min.js', 'toastr/toastr.min.js');
copyFile('node_modules/toastr/build/toastr.min.css', 'toastr/toastr.min.css');
copyFile('node_modules/chart.js/dist/chart.umd.min.js', 'chart.js/chart.umd.min.js');
copyFile(
  'node_modules/chart.js/dist/chart.umd.min.js.map',
  'chart.js/chart.umd.min.js.map'
);
copyFile(
  'node_modules/chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.min.js',
  'chartjs-adapter-date-fns/chartjs-adapter-date-fns.bundle.min.js'
);
copyFile(
  'node_modules/@fortawesome/fontawesome-free/css/all.min.css',
  'fontawesome/css/all.min.css'
);
copyDirectory(
  'node_modules/@fortawesome/fontawesome-free/webfonts',
  'fontawesome/webfonts'
);

const interWeights = [400, 500, 600, 700, 800];
const interCss = interWeights.map(weight => `@font-face {
  font-family: 'Inter';
  font-style: normal;
  font-display: swap;
  font-weight: ${weight};
  src: url('./files/inter-latin-${weight}-normal.woff2') format('woff2');
}`).join('\n\n');
mkdirSync(join(vendorRoot, 'inter', 'files'), { recursive: true });
writeFileSync(join(vendorRoot, 'inter', 'inter.css'), `${interCss}\n`, 'utf8');
for (const weight of interWeights) {
  copyFile(
    `node_modules/@fontsource/inter/files/inter-latin-${weight}-normal.woff2`,
    `inter/files/inter-latin-${weight}-normal.woff2`
  );
}

const licenses = [
  ['tailwindcss', ['node_modules/tailwindcss/LICENSE']],
  ['jquery', ['node_modules/jquery/LICENSE.txt']],
  [
    'toastr',
    [
      'node_modules/toastr/LICENSE',
      'node_modules/toastr/LICENSE.txt',
      'frontend/licenses/toastr-MIT.txt'
    ]
  ],
  ['chart.js', ['node_modules/chart.js/LICENSE.md', 'node_modules/chart.js/LICENSE']],
  [
    'chartjs-adapter-date-fns',
    [
      'node_modules/chartjs-adapter-date-fns/LICENSE.md',
      'node_modules/chartjs-adapter-date-fns/LICENSE'
    ]
  ],
  [
    'fontawesome-free',
    [
      'node_modules/@fortawesome/fontawesome-free/LICENSE.txt',
      'node_modules/@fortawesome/fontawesome-free/LICENSE'
    ]
  ],
  ['inter', ['node_modules/@fontsource/inter/LICENSE']]
];
for (const [name, candidates] of licenses) {
  copyFirstExisting(candidates, `licenses/${name}.txt`);
}

const dependencyNames = [
  'tailwindcss',
  'jquery',
  'toastr',
  'chart.js',
  'chartjs-adapter-date-fns',
  '@fortawesome/fontawesome-free',
  '@fontsource/inter'
];
const packages = Object.fromEntries(
  dependencyNames.map(name => {
    const metadata = packageMetadata(name);
    const declaredLicense = metadata.license || (metadata.licenses || [])
      .map(item => item.type)
      .filter(Boolean)
      .join(' AND ');
    return [name, {
      version: metadata.version,
      license: declaredLicense || 'SEE_LICENSE_FILE'
    }];
  })
);
const assets = Object.fromEntries(
  collectFiles(vendorRoot).map(path => [
    relative(vendorRoot, path).replaceAll('\\', '/'),
    sha256(path)
  ])
);
const manifest = {
  schema_version: 1,
  generated_by: 'scripts/build_frontend.mjs',
  packages,
  assets
};
writeFileSync(
  join(vendorRoot, 'manifest.json'),
  `${JSON.stringify(manifest, null, 2)}\n`,
  'utf8'
);
