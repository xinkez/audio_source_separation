import numpy as np

EPS=1e-12

class ILRMAbase:
    def __init__(self, n_bases=10, eps=EPS):
        self.input = None
        self.n_bases = n_bases

        self.eps = eps
    
    def _reset(self):
        assert self.input is not None, "Specify data!"

        n_bases = self.n_bases
        eps = self.eps 

        X = self.input

        n_channels, n_bins, n_frames = X.shape

        self.n_channels, self.n_sources = n_channels, n_channels # n_channels == n_sources
        self.n_bins, self.n_frames = n_bins, n_frames

        W = np.eye(n_channels, dtype=np.complex128)
        self.demix_filter = np.tile(W, reps=(n_bins, 1, 1))
        self.base = np.random.rand(n_channels, n_bins, n_bases)
        self.activation = np.random.rand(n_channels, n_bases, n_frames)
        self.estimation = self.separate(X, demix_filter=W)
        
    def __call__(self, input, iteration=100):
        """
        Args:
            input (n_channels, n_bins, n_frames)
        Returns:
            output (n_channels, n_bins, n_frames)
        """
        self.input = input

        self._reset()

        for idx in range(iteration):
            self.update_once()
        
        X, W = input, self.demix_filter
        output = self.separate(X, demix_filter=W)

        return output

    def update_once(self):
        raise NotImplementedError("Implement 'update' function")
    
    def separate(self, input, demix_filter):
        """
        Args:
            input (n_channels, n_bins, n_frames): 
            demix_filter (n_bins, n_sources, n_channels): 
        Returns:
            output (n_channels, n_bins, n_frames): 
        """
        input = input.transpose(1,0,2)
        estimation = demix_filter @ input
        output = estimation.transpose(1,0,2)

        return output

class GaussILRMA(ILRMAbase):
    def __init__(self, n_bases=10, reference_id=0, eps=EPS):
        super().__init__(n_bases=n_bases, eps=eps)

        self.reference_id = reference_id

    def update_once(self):
        reference_id = self.reference_id

        X = self.input

        self.update_source_model()
        self.update_space_model()

        W = self.demix_filter
        Y = self.separate(X, demix_filter=W)
        
        scale = projection_back(Y, reference=X[self.reference_id])

        Y_hat = Y * scale[...,np.newaxis].conj() # (N, I, J)

        _Y_hat = Y_hat.transpose(1,0,2) # (I, N, J)
        _X = X.transpose(1,0,2) # (I, M, J)
        X_Hermite = X.transpose(1,2,0).conj() # (I, J, M)
        XX_inverse = np.linalg.inv(_X @ X_Hermite)
        self.demix_filter = _Y_hat @ X_Hermite @ XX_inverse
        self.estimation = Y_hat
    
    def update_source_model(self):
        eps = self.eps

        X, W = self.input, self.demix_filter
        estimation = self.separate(X, demix_filter=W)
        P = np.abs(estimation)**2

        T, V = self.base, self.activation

        # Update bases
        V_transpose = V.transpose(0,2,1)
        TV = T @ V
        TV[TV < eps] = eps
        division, TV_inverse = P / TV**2, 1 / TV
        TVV = TV_inverse @ V_transpose
        TVV[TVV < eps] = eps
        T = T * np.sqrt(division @ V_transpose / TVV)
        T[T < eps] = eps

        # Update activations
        T_transpose = T.transpose(0,2,1)
        TV = T @ V
        TV[TV < eps] = eps
        division, TV_inverse = P / (TV**2), 1 / TV
        TTV = T_transpose @ TV_inverse
        TTV[TTV < eps] = eps
        V = V * np.sqrt(T_transpose @ division / TTV)
        V[V < eps] = eps

        self.bases, self.activation = T, V

    def update_space_model(self):
        n_sources = self.n_sources
        n_bins = self.n_bins
        eps = self.eps

        X, W = self.input, self.demix_filter
        TV = self.base @ self.activation
        
        X = X.transpose(1,2,0) # (n_bins, n_frames, n_channels)
        X = X[...,np.newaxis]
        X_Hermite = X.transpose(0,1,3,2).conj()
        XX = X @ X_Hermite # (n_bins, n_frames, n_channels, n_channels)
        R = TV[...,np.newaxis, np.newaxis] # (n_sources, n_bins, n_frames, 1, 1)
        U = XX / R
        U = U.mean(axis=2) # (N, n_bins, n_channels, n_channels)

        for source_idx in range(n_sources):
            # W: (n_bins, N, n_channels), U: (N, n_bins, n_channels, n_channels)
            U_n = U[source_idx] # (n_bins, n_channels, n_channels)
            WU = W @ U_n # (n_bins, n_sources, n_channels)
            # TODO: condition number
            WU_inverse = np.linalg.inv(WU)[...,source_idx] # (n_bins, n_sources, n_channels)
            w = WU_inverse.conj() # (n_bins, n_channels)
            wUw = w[:, np.newaxis, :] @ U_n @ w[:, :, np.newaxis].conj()
            denominator = np.sqrt(wUw[...,0])
            W[:, source_idx, :] = w / denominator

        self.demix_filter = W

def projection_back(Y, reference):
    """
    Args:
        Y: (n_channels, n_bins, n_frames)
        ref: (n_bins, n_frames)
    Returns:
        scale: (n_channels, n_bins)
    """
    n_channels, n_bins, _ = Y.shape

    numerator = np.sum(Y * reference.conj(), axis=2) # (n_channels, n_bins)
    denominator = np.sum(np.abs(Y)**2, axis=2) # (n_channels, n_bins)
    scale = np.ones((n_channels, n_bins), dtype=np.complex128)
    indices = denominator > 0.0
    scale[indices] = numerator[indices] / denominator[indices]

    return scale


def _convolve_mird(titles, reverb=0.160, degrees=[0], mic_indices=[0], samples=None):
    mixed_signals = []

    for mic_idx in mic_indices:
        _mixture = 0
        for title_idx in range(len(titles)):
            degree = degrees[title_idx]
            title = titles[title_idx]
            rir_path = "data/MIRD/Reverb{:.3f}_8-8-8-8-8-8-8/Impulse_response_Acoustic_Lab_Bar-Ilan_University_(Reverberation_{:.3f}s)_8-8-8-8-8-8-8_1m_{:03d}.mat".format(reverb, reverb, degree)
            rir_mat = loadmat(rir_path)

            rir = rir_mat['impulse_response']

            if samples is not None:
                rir = rir[:samples]

            source, sr = read_wav("data/single-channel/{}.wav".format(title))
            _mixture = _mixture + np.convolve(source, rir[:, mic_idx])
        
        mixed_signals.append(_mixture)
    
    mixed_signals = np.array(mixed_signals)

    return mixed_signals

def _test():
    np.random.seed(111)
    
    # Room impulse response
    reverb = 0.16
    duration = 0.5
    samples = int(duration * 16000)
    mic_indices = [2, 5]
    degrees = [60, 300]
    titles = ['man-16000', 'woman-16000']

    mixed_signal = _convolve_mird(titles, reverb=reverb, degrees=degrees, mic_indices=mic_indices, samples=samples)

    n_sources, T = mixed_signal.shape
    
    # STFT
    fft_size, hop_size = 2048, 1024
    
    mixture = []
    for _mixed_signal in mixed_signal:
        _mixture = stft(_mixed_signal, fft_size=fft_size, hop_size=hop_size)
        mixture.append(_mixture)
    mixture = np.array(mixture)

    # ILRMA
    n_bases = 10
    n_channels = len(titles)
    iteration = 100

    gauss_ilrma = GaussILRMA(n_bases=n_bases)
    estimation = gauss_ilrma(mixture, iteration=iteration)

    estimated_signal = []
    for _estimation in estimation:
        _estimated_signal = istft(_estimation, fft_size=fft_size, hop_size=hop_size, length=T)
        estimated_signal.append(_estimated_signal)
    estimated_signal = np.array(estimated_signal)
    
    print("Mixture: {}, Estimation: {}".format(mixed_signal.shape, estimated_signal.shape))

    for idx in range(n_channels):
        _estimated_signal = estimated_signal[idx]
        write_wav("data/ILRMA/mixture-16000_estimated-iter{}-{}.wav".format(iteration, idx), signal=_estimated_signal, sr=16000)

def _test_conv():
    reverb = 0.16
    duration = 0.5
    samples = int(duration * 16000)
    mic_indices = [2, 5]
    degrees = [60, 300]
    titles = ['man-16000', 'woman-16000']
    
    mixed_signal = _convolve_mird(titles, reverb=reverb, degrees=degrees, mic_indices=mic_indices, samples=samples)

    write_wav("data/multi-channel/mixture-16000.wav", mixed_signal.T, sr=16000)

if __name__ == '__main__':
    import os
    import matplotlib.pyplot as plt
    import numpy as np
    from scipy.io import loadmat

    from utils.utils_audio import read_wav, write_wav
    from algorithm.stft import stft, istft

    plt.rcParams['figure.dpi'] = 200

    os.makedirs("data/multi-channel", exist_ok=True)
    os.makedirs("data/ILRMA", exist_ok=True)

    # _test_conv()
    _test()