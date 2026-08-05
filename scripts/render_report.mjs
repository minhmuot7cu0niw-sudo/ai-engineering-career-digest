import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import process from "node:process";
import { chromium } from "playwright";

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith("--") || !argv[index + 1] || argv[index + 1].startsWith("--")) {
      throw new Error(`invalid argument: ${key ?? "<missing>"}`);
    }
    args[key.slice(2)] = argv[index + 1];
    index += 1;
  }
  if (!args.input || !args.output) {
    throw new Error("--input and --output are required");
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const inputPath = resolve(args.input);
  const outputPath = resolve(args.output);
  await mkdir(resolve(outputPath, ".."), { recursive: true });

  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1080, height: 1000 },
      deviceScaleFactor: 1,
    });
    await page.goto(pathToFileURL(inputPath).href, { waitUntil: "load" });
    await page.evaluate(() => document.fonts?.ready);
    await page.screenshot({ path: outputPath, fullPage: true });
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
