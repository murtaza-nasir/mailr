import { describe, expect, it } from 'vitest'
import { prefixMatch, rankPeople } from './recipientRank'

const jordan = { name: 'Jordan Lee', email: 'jordan@bluefin.example' }
const dana = { name: 'Dana Whitfield', email: 'dana@lumencoffee.example' }
const sam = { name: 'Sam Okafor', email: 'sam.dando@northwind.example' }

describe('recipient suggestions', () => {
  it('"dan" ranks Dana before Jordan, though Jordan writes more', () => {
    expect(rankPeople([jordan, dana], 'dan').map((p) => p.name)).toEqual(['Dana Whitfield', 'Jordan Lee'])
  })
  it('a prefix of the last name or of a part of the address counts', () => {
    expect(prefixMatch(dana, 'whit')).toBe(true)
    expect(prefixMatch(sam, 'dan')).toBe(true)
    expect(prefixMatch(jordan, 'dan')).toBe(false)
  })
  it('keeps the server order within each group', () => {
    const a = { name: 'Danielle A', email: 'a@x.example' }
    const b = { name: 'Dan B', email: 'b@x.example' }
    expect(rankPeople([jordan, a, b], 'dan')).toEqual([a, b, jordan])
  })
})
