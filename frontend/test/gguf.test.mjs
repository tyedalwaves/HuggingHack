import assert from 'node:assert/strict'
import test from 'node:test'

import { metadataPreview } from '../src/gguf.ts'

test('GGUF metadata arrays are summarized without retaining the full value', () => {
  assert.deepEqual(metadataPreview(['one', 'two', 'three', 'four', 'five']), {
    value: '["one", "two", "three", "four", …]',
    itemCount: 5,
  })
  assert.deepEqual(metadataPreview([[1, 2, 3], true]), {
    value: '[[3 items], true]',
    itemCount: 2,
  })
})

test('GGUF scalar metadata is readable and bounded', () => {
  assert.deepEqual(metadataPreview(32n), { value: '32' })
  assert.deepEqual(metadataPreview('line one\r\nline two'), {
    value: 'line one\nline two',
  })
  assert.equal(metadataPreview('x'.repeat(600)).value.length, 501)
})
