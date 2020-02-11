import torch
from brnolm.data_pipeline.masked import masked_tensor_from_sentences


class LanguageModel(torch.nn.Module):
    def __init__(self, model, decoder, vocab):
        super().__init__()

        self.model = model
        self.decoder = decoder
        self.vocab = vocab

    def forward(self, x, h0):
        o, h = self.model(x, h0)
        return self.decoder(o), h

    @property
    def device(self):
        return next(self.parameters()).device

    def single_sentence_nll(self, sentence, prefix):
        '''Provides the negative log-probability of a sequence of tokens
        '''
        sentence_ids = [self.vocab[c] for c in sentence]
        device = self.device

        if prefix:
            prefix_id = self.vocab[prefix]
            tensor = torch.tensor([prefix_id] + sentence_ids).view(1, -1).to(device)
        else:
            tensor = torch.tensor(sentence_ids).view(1, -1).to(device)

        h0 = self.model.init_hidden(1)
        o, _ = self.model(tensor[:, :-1], h0)

        if prefix:
            nll, _ = self.decoder.neg_log_prob(o, tensor[:, 1:])
        else:
            o0 = self.model.extract_output_from_h(h0).unsqueeze(1)
            prepended_o = torch.cat([o0, o], dim=1)
            nll, _ = self.decoder.neg_log_prob(prepended_o, tensor)

        return nll.item()

    def batch_nll(self, sentences, prefix):
        '''Provides the negative log-probability of a batch of sequences of tokens
        '''
        if not sentences:
            return []

        idx_seqs = [[self.vocab[w] for w in s] for s in sentences]

        h0_provider = self.get_custom_h0_provider(prefix)
        masked_nlllh = self.batch_nll_idxs(idx_seqs, h0_provider=h0_provider)

        return masked_nlllh.sum(dim=1).detach().cpu().numpy().tolist()

    def batch_nll_idxs(self, idxs, h0_provider=None):
        '''Provides the negative log-probability of a batch of sequences of indexes
        '''
        if h0_provider is None:
            h0_provider = self.model.init_hidden

        device = self.device
        input, target, mask = masked_tensor_from_sentences(idxs, device=device, target_all=True)
        batch_size = input.shape[0]

        h0 = h0_provider(batch_size)
        o, _ = self.model(input, h0)

        o0 = self.model.extract_output_from_h(h0).unsqueeze(1)
        o = torch.cat([o0, o], dim=1)

        all_nlllh = self.decoder.neg_log_prob_raw(o, target)

        return all_nlllh * mask

    def get_custom_h0_provider(self, prefix):
        if not prefix:
            return self.model.init_hidden

        prefix_ids = [self.vocab[c] for c in prefix]
        device = self.device

        prefix_tensor = torch.tensor(prefix_ids).view(1, -1).to(device)

        def h0_provider(batch_size):
            h0 = self.model.init_hidden(batch_size)
            _, h = self.model(torch.cat([prefix_tensor]*batch_size, axis=0), h0)
            return h

        return h0_provider
