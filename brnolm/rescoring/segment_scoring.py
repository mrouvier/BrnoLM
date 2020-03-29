from dataclasses import dataclass
import logging
import typing
import torch

from brnolm.language_models.language_model import split_batch_hidden_state, detach_hidden_state


# for LSTMs
def lstm_h0_provider(single_h, batch_size):
    h, c = single_h
    return (torch.stack([h]*batch_size, axis=1), torch.stack([h]*batch_size, axis=1))


@dataclass
class SegmentScoringResult:
    scores: typing.Dict[str, float]
    hidden_states: typing.List[torch.Tensor]


class SegmentScorer:
    def __init__(self, lm, out_f, max_softmaxes=2000):
        self.lm = lm
        self.out_f = out_f
        self.max_softmaxes = max_softmaxes

    def process_segment(self, seg_name, seg_hyps, custom_h0=None):
        nb_hyps = len(seg_hyps)
        min_len = min(len(hyp) for hyp in seg_hyps.values())
        max_len = max(len(hyp) for hyp in seg_hyps.values())
        total_len = sum(len(hyp) for hyp in seg_hyps.values())
        nb_oovs = sum(sum(token == self.lm.vocab.unk_word for token in hyp) for hyp in seg_hyps.values())
        logging.info(f"{seg_name}: {nb_hyps} hypotheses, min/max/avg length {min_len}/{max_len}/{total_len/nb_hyps:.1f} tokens, # OOVs {nb_oovs}")

        if custom_h0:
            h0_provider = lambda batch_size: lstm_h0_provider(custom_h0, batch_size)
        else:
            h0_provider = self.lm.get_custom_h0_provider('</s>')

        X, rev_map = self.dict_to_list(seg_hyps)  # reform the word sequences
        ys, hs = self.get_scores(X, h0_provider)

        return SegmentScoringResult(
            {rev_map[i]: lm_cost for i, lm_cost in enumerate(ys)},
            {rev_map[i]: h for i, h in enumerate(hs)},
        )

    def dict_to_list(self, utts_map):
        list_of_lists = []
        rev_map = {}
        for key in utts_map:
            rev_map[len(list_of_lists)] = key
            list_of_lists.append(utts_map[key])

        return list_of_lists, rev_map

    def get_scores(self, hyps, h0_provider):
        work_left = [hyps]
        ys = []
        hs = []

        while work_left:
            batch = work_left.pop(0)
            try:
                if len(batch) * max(len(s) for s in batch) > self.max_softmaxes:
                    raise RuntimeError("Preemptive, batch is {len(batch)}x{max(len(s) for s in batch)}")
                idxs = [[self.lm.vocab[w] for w in s] for s in batch]
                this_batch_ys, this_batch_hs = self.lm.batch_nll_idxs(idxs, h0_provider, return_h=True)
                ys.extend(this_batch_ys.sum(axis=1).detach().numpy())
                hs.extend(split_batch_hidden_state(detach_hidden_state(this_batch_hs)))

            except RuntimeError as e:
                cuda_memory_error = 'CUDA out of memory' in str(e)
                cpu_memory_error = "can't allocate memory" in str(e)
                preemtive_memory_error = "Preemptive" in str(e)
                assert cuda_memory_error or cpu_memory_error or preemtive_memory_error
                midpoint = len(batch) // 2
                assert midpoint > 0
                first, second = batch[:midpoint], batch[midpoint:]
                work_left.insert(0, second)
                work_left.insert(0, first)
        return ys, hs