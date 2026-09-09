# Attention Is All You Need

15 pages, read from `attention.pdf`

## The question

Can a sequence transduction model based entirely on attention mechanisms, without recurrence or convolutions, match or exceed the performance of state-of-the-art recurrent/convolutional encoder-decoder models on translation and other tasks while being more parallelizable and faster to train?

## What they did

The authors propose the Transformer, an encoder-decoder architecture built solely from stacked self-attention and point-wise fully connected layers (6 identical layers each for encoder and decoder). They introduce scaled dot-product attention and multi-head attention (8 heads), position-wise feed-forward networks, sinusoidal positional encodings to inject sequence order information, and residual connections with layer normalization around each sub-layer. The decoder uses masked self-attention to preserve auto-regressive prediction. They train base and 'big' model variants on WMT 2014 English-to-German (4.5M sentence pairs, byte-pair encoding, 37K vocab) and English-to-French (36M sentences, word-piece, 32K vocab) translation tasks using 8 NVIDIA P100 GPUs, the Adam optimizer with a custom learning rate schedule, residual dropout, and label smoothing. They compare BLEU scores and training cost (FLOPs) against prior state-of-the-art models, run ablation studies varying architecture hyperparameters (attention heads, key/value dimensions, model size, dropout, positional encoding type), and test generalization by applying the Transformer to English constituency parsing on the Penn Treebank (WSJ), both in a small-data-only setting and a semi-supervised setting.

## Findings

- The Transformer (big) model achieves 28.4 BLEU on WMT 2014 English-to-German, improving over previous best results (including ensembles) by more than 2 BLEU.  (p.1)
- The Transformer (big) model establishes a new single-model state-of-the-art BLEU score of 41.8 on WMT 2014 English-to-French after training for 3.5 days on eight GPUs.  (p.1)
- The base Transformer model surpasses all previously published models and ensembles on English-to-German at a fraction of the training cost of competitive models.  (p.8)
- In the Transformer, the number of operations to relate signals from two arbitrary positions is reduced to a constant (O(1)), unlike ConvS2S (linear) and ByteNet (logarithmic), counteracted for reduced resolution by multi-head attention.  (p.2)
- Self-attention layers have O(1) sequential operations and maximum path length, compared to O(n) sequential operations for recurrent layers, making self-attention more parallelizable and better at learning long-range dependencies when sequence length n is smaller than representation dimension d.  (p.6)
- Single-head attention is 0.9 BLEU worse than the best multi-head setting, and quality also drops with too many heads.  (p.9)
- Reducing the attention key size dk hurts model quality, suggesting determining compatibility is not easy and a more sophisticated compatibility function than dot product may help.  (p.9)
- Bigger models perform better, and dropout is very helpful in avoiding overfitting.  (p.9)
- Replacing sinusoidal positional encoding with learned positional embeddings produces nearly identical results to the base model.  (p.9)
- The Transformer generalizes well to English constituency parsing, achieving 91.3 F1 (WSJ only) and 92.7 F1 (semi-supervised) on Section 23 of WSJ, outperforming most previously reported models including BerkeleyParser even when trained only on the 40K-sentence WSJ set.  (p.10)
- Attention heads appear to learn distinct, interpretable behaviors, including tracking long-distance dependencies and performing anaphora resolution, related to syntactic and semantic sentence structure.  (p.6)
- The Transformer can reach a new state of the art in translation quality after training for as little as twelve hours on eight P100 GPUs.  (p.2)

## Limitations

- Self-attention's benefit over recurrent layers in path length holds primarily when sequence length n is smaller than representation dimensionality d, which is common but not universal for sentence representations.  (p.7)
- A single convolutional layer with kernel width k < n does not connect all pairs of positions, requiring stacks of layers, and separable convolutions with k=n have complexity equal to the combined self-attention plus feed-forward approach used here, indicating no strict complexity advantage over convolutions.  (p.7)
- The full self-attention complexity per layer is O(n^2 * d), which could become costly for very long sequences.  (p.6)
- For the English-to-French big model, a lower dropout rate (0.1 instead of 0.3) was needed, indicating hyperparameter sensitivity across datasets.  (p.8)
- Label smoothing hurts perplexity, as the model learns to be more unsure, even though it improves accuracy and BLEU score.  (p.8)

## Numbers

| value | means | page |
|---|---|---|
| 28.4 BLEU | Transformer (big) score on WMT 2014 English-to-German translation task | p.1 |
| 41.8 BLEU | Transformer (big) single-model state-of-the-art score on WMT 2014 English-to-French translation task | p.1 |
| 3.5 days on 8 GPUs | Training time for the Transformer (big) model on English-to-French | p.1 |
| 12 hours on 8 P100 GPUs | Minimum training time for Transformer to reach new state of the art in translation quality | p.2 |
| N = 6 | Number of identical layers in both the encoder and decoder stacks | p.3 |
| dmodel = 512 | Dimension of embeddings and sub-layer outputs in the base model | p.3 |
| h = 8 | Number of parallel attention heads used in multi-head attention | p.5 |
| dk = dv = 64 | Dimension of keys/values per attention head (dmodel/h) | p.5 |
| dff = 2048 | Inner-layer dimensionality of the position-wise feed-forward network | p.5 |
| 4.5 million sentence pairs | Size of WMT 2014 English-German training dataset | p.7 |
| 36M sentences | Size of WMT 2014 English-French training dataset | p.7 |
| 37000 tokens | Shared source-target byte-pair encoding vocabulary size for English-German | p.7 |
| 32000 word-piece vocabulary | Vocabulary size used for English-French dataset | p.7 |
| 25000 source / 25000 target tokens | Approximate tokens per training batch | p.7 |
| 100,000 steps / 12 hours | Training duration for base models | p.7 |
| 300,000 steps / 3.5 days | Training duration for big models | p.7 |
| beta1=0.9, beta2=0.98, epsilon=10^-9 | Adam optimizer hyperparameters | p.7 |
| Pdrop = 0.1 | Residual dropout rate for the base model | p.8 |
| label smoothing = 0.1 | Value of label smoothing used during training | p.8 |
| 3.3 x 10^18 FLOPs (EN-DE), 2.3 x 10^19 FLOPs (EN-FR) | Estimated training cost of Transformer (big) model | p.8 |
| 91.3 F1 / 92.7 F1 | Transformer (4 layers) results on WSJ-only and semi-supervised English constituency parsing respectively | p.10 |
| beam size 4, length penalty alpha=0.6 | Inference hyperparameters used for translation tasks | p.8 |
| beam size 21, alpha=0.3 | Inference hyperparameters used for constituency parsing experiments | p.10 |

## Gaps

- The paper does not explore restricted/local self-attention for very long sequences (e.g., images, audio, video), leaving efficient handling of large inputs/outputs as future work.
- The paper does not investigate making generation (decoding) less sequential, which is noted as a future research goal.
- The paper does not extend the Transformer to non-text input/output modalities, leaving this as planned future work.
- The interpretability findings from attention visualization are only illustrative (qualitative examples) and not systematically evaluated or quantified.
- The paper does not deeply investigate more sophisticated compatibility functions than dot product, despite noting that reduced key dimension hurts quality, suggesting dot product may not be optimal.
