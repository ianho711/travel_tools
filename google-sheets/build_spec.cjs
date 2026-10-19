const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, 'Code.gs'), 'utf8');
const T = vm.runInNewContext(source + '\nTravelSheets;');
const model = process.argv[2] ? JSON.parse(fs.readFileSync(process.argv[2], 'utf8')) : T.create();
process.stdout.write(JSON.stringify(T.build(model)));
