import { compileFromFile } from 'json-schema-to-typescript';
import { readdir, writeFile, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../../', import.meta.url));
const check = process.argv.includes('--check');
for (const filename of (await readdir(path.join(root, 'schemas'))).sort()) {
  if (!filename.endsWith('.schema.json')) continue;
  const content = await compileFromFile(path.join(root, 'schemas', filename), {
    bannerComment: '/* 此文件由 schemas/ 生成，请勿手改。pnpm generate 可重新生成。 */',
    style: { singleQuote: true },
  });
  const target = path.join(root, 'frontend/src/contracts', filename.replace('.schema.json', '.ts'));
  if (check) {
    if (await readFile(target, 'utf8') !== content) throw new Error(`协议类型需要重新生成：${target}`);
  } else {
    await writeFile(target, content);
  }
}
