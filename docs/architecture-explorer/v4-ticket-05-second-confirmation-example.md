> **隔离测试示例。** 评论、Receipt 与 Anchor 均为 fixture，不能用于真实审核或生产安装。

# 第二次确认：可信评论与精确 Anchor 清单

> 评论发布和 Receipt 均不等于 Anchor 授权。请独立核对本批精确集合；本次确认不覆盖未来评论或其他对象。

- 状态：**READY**
- 首次确认：`2539db73ff37845cc1aae51b38c2e044efaed21599e5e84164aa0e4f878dc66a`
- Receipt 批次：`4a456dab4edb28c00bcb3e9f2a790f955c2e602516e7816f951721ad3bb93846`

## 1. 候选协作 — `candidate-collaboration::relationship-under-review`

- 决定：**REJECTED**；理由：证据不足
- 评论：`github-comment:9001`；https://github.com/nnewlander/translate/issues/79#issuecomment-9001
- 作者：nnewlander（OWNER / admin）
- 时间：创建 `2026-11-26T10:00:00Z`；更新 `2026-11-26T10:01:00Z`
- Receipt：`087af9eed049e39689df1134a65a27ed1c03ab7544f9fe77894c5abc32670020`
- 评论正文 SHA-256：`1c6d976faf91ab8a498224d2a006389df39aabb02dbf4e46fee4846ac86976ca`
> ARCHITECTURE_EXPLORER_RELATIONSHIP_REVIEW_V1
> Decision-ID: github-comment:9001
> Result: REJECTED
> Object-Key: candidate-collaboration + relationship-under-review
> Claims-SHA256: 8eccad16a1be1ec04728b352fd17a83fa2d12022b9954967548f00c27691f93d
> Candidate-Fact-SHA256: deda03bfa44572833f0893f925bf373e5b8e94f35190b17c643b12cc5dc8f427
> Reviewed-Revision: 09aaf4a033123c7eca8d280fa8982e5b0e711b86
> Publication-Key: 4076b045b05d87080167e2aedd983f03dff831bef34c526faa8eb2df59d5d9d9
- Anchor：`{"comment_created_at": "2026-11-26T10:00:00Z", "comment_id": 9001, "comment_updated_at": "2026-11-26T10:01:00Z", "issue_id": 6599421144, "issue_number": 79, "source_receipt_sha256": "087af9eed049e39689df1134a65a27ed1c03ab7544f9fe77894c5abc32670020"}`
- 异常：无

## 2. 候选分解 — `candidate-decomposition::decomposition-under-review`

- 决定：**APPROVED**；理由：认可完整静态范围
- 评论：`github-comment:9002`；https://github.com/nnewlander/translate/issues/79#issuecomment-9002
- 作者：nnewlander（OWNER / admin）
- 时间：创建 `2026-11-26T10:00:00Z`；更新 `2026-11-26T10:01:00Z`
- Receipt：`aacf262adc5558f44e3514f18cce4d5a7327cc77e0f5fc3e31d468709ce8923b`
- 评论正文 SHA-256：`cdafae8ffd722400d2fa5fd73e13cd37d3fc54d637ac319936db04bef21fa596`
> ARCHITECTURE_EXPLORER_RELATIONSHIP_REVIEW_V1
> Decision-ID: github-comment:9002
> Result: APPROVED
> Object-Key: candidate-decomposition + decomposition-under-review
> Claims-SHA256: fb7c44567776cf1ca549380afe730342b0b740c15ca60ff9d64fdc6861303639
> Candidate-Fact-SHA256: ee95b860214d994cc6b57eac32eb40efa624e628dd35d23d6fca30423ad92f91
> Reviewed-Revision: 09aaf4a033123c7eca8d280fa8982e5b0e711b86
> Publication-Key: 0982ac796c43fee6a61e6ea18ced0360e61b3ce48546a4036bec41d84bab962a
- Anchor：`{"comment_created_at": "2026-11-26T10:00:00Z", "comment_id": 9002, "comment_updated_at": "2026-11-26T10:01:00Z", "issue_id": 6599421144, "issue_number": 79, "source_receipt_sha256": "aacf262adc5558f44e3514f18cce4d5a7327cc77e0f5fc3e31d468709ce8923b"}`
- 异常：无

## 3. Worker 入口 — `candidate-node::worker-entry`

- 决定：**APPROVED**；理由：认可完整静态范围
- 评论：`github-comment:9003`；https://github.com/nnewlander/translate/issues/79#issuecomment-9003
- 作者：nnewlander（OWNER / admin）
- 时间：创建 `2026-11-26T10:00:00Z`；更新 `2026-11-26T10:01:00Z`
- Receipt：`b73c625f0f8e1392d8c1b7a833aa84d9299b8ff51c49d3a0a844de0336ca348f`
- 评论正文 SHA-256：`444bceb6dc62d97d515095921a154f4869fd2e5d7441edbdfdac2ffc552a3a3c`
> ARCHITECTURE_EXPLORER_NODE_REVIEW_V2
> Decision-ID: github-comment:9003
> Result: APPROVED
> Object-Key: candidate-node + worker-entry
> Claims-SHA256: 90049379d8e46a611390500027187f85269a0ec0a89e598f942d53fec71df601
> Candidate-Fact-SHA256: 0833eecd66da0eba069ad196fab38fa538ceb23a0d12eb6799f14114d6da7718
> Reviewed-Revision: 09aaf4a033123c7eca8d280fa8982e5b0e711b86
> Publication-Key: 9b4ef8e54d132d173a85889dd67e21cee2614d77298a9a53d26a2cdbafc611ec
- Anchor：`{"comment_created_at": "2026-11-26T10:00:00Z", "comment_id": 9003, "comment_updated_at": "2026-11-26T10:01:00Z", "issue_id": 6599421144, "issue_number": 79, "source_receipt_sha256": "b73c625f0f8e1392d8c1b7a833aa84d9299b8ff51c49d3a0a844de0336ca348f"}`
- 异常：无

## 本批精确授权集合

本次授权仅覆盖以下 3 个 Anchor：

- `candidate-collaboration::relationship-under-review` → `github-comment:9001`
- `candidate-decomposition::decomposition-under-review` → `github-comment:9002`
- `candidate-node::worker-entry` → `github-comment:9003`

## 异常汇总

异常：无

请在核对无误后，一次确认授权宿主登记并安装本批精确 Anchor。该授权仅适用于此清单。
