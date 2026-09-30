"""Actual Python build-logic tests. CMake strings are fixtures, NOT SDK builds."""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_rust', ROOT / 'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

class RustBuildTooling(unittest.TestCase):
    @staticmethod
    def fixture():
        return ('COMMAND em++ ${PHYSXWASM_GLUE_WRAPPER} ${EMCC_GLUE_ARGS} -o glue.o\n'
                'COMMAND em++ glue.o ${PHYSX_LIBS} ${EMCC_WASM_ARGS} -o physx-js-webidl.mjs\n'
                'DEPENDS physx-js-bindings ${PHYSX_TARGETS}\n')
    def patch(self, text=None):
        return build.patch_rust(self.fixture() if text is None else text,
                                '/tmp/shim.cpp', '/tmp/abi.h', '/tmp/rust.a')
    def test_same_module_link(self):
        text = self.patch()
        self.assertIn('glue.o pr_bulk_rust.o -lc -lcompiler_rt "/tmp/rust.a" ${PHYSX_LIBS}', text)
        self.assertEqual(text.count('-o physx-js-webidl.mjs'), 1)
    def test_addon_cplusplus_flags(self):
        text = self.patch()
        self.assertIn('${EMCC_GLUE_ARGS} -std=c++17 -msimd128 -fno-rtti -fno-exceptions', text)
    def test_dependencies_include_archive_header(self):
        text = self.patch()
        self.assertIn('DEPENDS physx-js-bindings ${PHYSX_TARGETS} pr_bulk_rust.o "/tmp/rust.a"', text)
        self.assertIn('DEPENDS "/tmp/shim.cpp" "/tmp/abi.h"', text)
    def test_patch_idempotent(self):
        text = self.patch()
        self.assertEqual(text, self.patch(text))
    def test_existing_addon_gains_matching_glue_flags(self):
        fixed = self.patch()
        flags = ' -std=c++17 -msimd128 -fno-rtti -fno-exceptions'
        legacy = fixed.replace('${EMCC_GLUE_ARGS}' + flags + ' -o glue.o', '${EMCC_GLUE_ARGS} -o glue.o')
        self.assertEqual(self.patch(legacy), fixed)
    def test_existing_addon_gains_matching_compiler_runtime(self):
        fixed = self.patch()
        self.assertEqual(self.patch(fixed.replace(' -lc -lcompiler_rt ', ' ')), fixed)
    def test_blast_joins_the_same_module_and_is_idempotent(self):
        root = ROOT / 'work/candidate/PhysX/blast'
        wrapper = ROOT / 'addons/blast/pr_blast_wasm.cpp'
        compat = ROOT / 'addons/blast/emscripten_nv_compat.h'
        fixed = build.patch_blast(self.patch(), root, wrapper, compat)
        self.assertIn('glue.o pr_bulk_rust.o pr_blast.o -lc -lcompiler_rt', fixed)
        self.assertIn('OUTPUT pr_blast.o', fixed)
        self.assertIn('physx-js-bindings ${PHYSX_TARGETS} pr_bulk_rust.o pr_blast.o', fixed)
        self.assertIn('-I "' + str(root / 'include/lowlevel').replace('\\', '/'), fixed)
        self.assertEqual(build.patch_blast(fixed, root, wrapper, compat), fixed)
        self.assertEqual(self.patch(fixed), fixed)
    def test_blast_refuses_changed_wrapper(self):
        root = ROOT / 'work/candidate/PhysX/blast'
        fixed = build.patch_blast(self.patch(), root,
            ROOT / 'addons/blast/pr_blast_wasm.cpp', ROOT / 'addons/blast/emscripten_nv_compat.h')
        with self.assertRaises(build.lab.LabError):
            build.patch_blast(fixed, root, ROOT / 'other.cpp',
                              ROOT / 'addons/blast/emscripten_nv_compat.h')
    def test_blast_upgrades_only_the_exact_legacy_addon_block(self):
        root = ROOT / 'work/candidate/PhysX/blast'
        wrapper = ROOT / 'addons/blast/pr_blast_wasm.cpp'
        compat = ROOT / 'addons/blast/emscripten_nv_compat.h'
        fixed = build.patch_blast(self.patch(), root, wrapper, compat)
        current = build.blast_addon(root, wrapper, compat, stress=True, authoring=True)
        legacy = build.blast_addon(root, wrapper, compat, stress=False)
        self.assertEqual(build.patch_blast(fixed.replace(current, legacy), root, wrapper, compat), fixed)
        stress_only = build.blast_addon(root, wrapper, compat, stress=True)
        self.assertEqual(build.patch_blast(fixed.replace(current, stress_only), root, wrapper, compat), fixed)
        for wrong in (legacy.replace('-O3', '-O0'), legacy.replace('NvBlastActor.cpp', 'Unknown.cpp'),
                      legacy.replace('  VERBATIM', '  COMMAND unwanted\n  VERBATIM'),
                      stress_only.replace('-mavx', '-mfma')):
            with self.subTest(block=wrong[-100:]), self.assertRaises(build.lab.LabError):
                build.patch_blast(fixed.replace(current, wrong), root, wrapper, compat)
    def test_blast_stress_uses_generated_adaptations_and_tracks_all_headers(self):
        root = ROOT / 'work/candidate/PhysX/blast'
        sources, includes = build.blast_layout(root)
        self.assertEqual(len(sources), 13)
        self.assertIn(root / 'source/sdk/globals/NvBlastGlobals.cpp', sources)
        text = build.patch_blast(self.patch(), root, ROOT / 'addons/blast/pr_blast_wasm.cpp',
                                 ROOT / 'addons/blast/emscripten_nv_compat.h')
        command = next(line for line in text.splitlines() if ' -o pr_blast.o' in line)
        self.assertIn('blast-stress-generated/NvBlastExtStressSolver.cpp', command)
        self.assertIn('blast-stress-generated/stress.cpp', command)
        self.assertNotIn('source/sdk/extensions/stress/NvBlastExtStressSolver.cpp', command)
        self.assertIn('-msimd128 -mavx', command)
        self.assertNotIn('-mfma', command)
        for path in build.blast_header_inputs(root):
            self.assertIn(build.cmake_literal(path), text)
        for name in build.evidence_tools.BLAST_BRIDGE:
            self.assertIn(build.cmake_literal(ROOT / name), text)
    def test_authoring_uses_the_proven_source_closure_and_exact_adaptation(self):
        root = ROOT / 'work/candidate/PhysX/blast'
        sources, includes = build.blast_layout(root, authoring=True)
        self.assertEqual(len(sources), 25)
        self.assertEqual(len(set(sources)), 25)
        extra, extra_includes = build.authoring_layout(root)
        self.assertEqual(len(extra), 12)
        self.assertTrue(set(extra) <= set(sources))
        self.assertTrue(set(extra_includes) <= set(includes))
        text = build.blast_addon(root, ROOT / 'addons/blast/pr_blast_wasm.cpp',
                                 ROOT / 'addons/blast/emscripten_nv_compat.h', stress=True, authoring=True)
        command = next(line for line in text.splitlines() if ' -o pr_blast.o' in line)
        self.assertIn('pr_blast_authoring.cpp', command)
        self.assertIn('blast-authoring-generated/NvBlastExtApexSharedParts.cpp', command)
        self.assertNotIn('authoring/NvBlastExtApexSharedParts.cpp', command)
        self.assertIn('blast-stress-generated/NvBlastExtStressSolver.cpp', command)
        self.assertIn('blast-stress-generated/stress.cpp', command)
        for path in [*sources, *build.blast_header_inputs(root, authoring=True)]:
            self.assertIn(build.cmake_literal(path), text)
        for path in build.evidence_tools.BLAST_BRIDGE:
            self.assertIn(build.cmake_literal(ROOT / path), text)
        for unsupported in ('VHACD', 'MeshCleaner', 'NvBlastExtAuthoring.cpp', 'ProcessFracture'):
            self.assertNotIn(unsupported, command)
    def test_authoring_cannot_omit_the_physical_family_stress_build(self):
        with self.assertRaisesRegex(build.lab.LabError, 'physical families'):
            build.blast_layout(ROOT / 'work/candidate/PhysX/blast', stress=False, authoring=True)
    def test_real_upstream_glue_uses_addon_exception_flags(self):
        original = (ROOT / 'port510/reference/PhysXWasmBindings.before.cmake').read_text()
        fixed = self.patch(original)
        self.assertIn('${EMCC_GLUE_ARGS} -std=c++17 -msimd128 -fno-rtti -fno-exceptions -o glue.o', fixed)
        self.assertEqual(self.patch(fixed), fixed)
    def test_changed_archive_refused(self):
        with self.assertRaises(build.lab.LabError):
            build.patch_rust(self.patch(), '/tmp/shim.cpp', '/tmp/abi.h', '/tmp/new.a')
    def test_old_addon_refused(self):
        with self.assertRaises(build.lab.LabError): self.patch('# PR_BULK_ADDON_V1\n' + self.fixture())
    def test_changed_anchor_refused(self):
        with self.assertRaises(build.lab.LabError): self.patch('different upstream CMake')
    def test_ambiguous_anchor_refused(self):
        with self.assertRaises(build.lab.LabError): self.patch(self.fixture() * 2)
    def test_cmake_metacharacters_refused(self):
        for path in ['a"b', 'a\nb', 'a;b', '${ROOT}/x', 'a\rb']:
            with self.subTest(path=path), self.assertRaises(build.lab.LabError):
                build.cmake_literal(path)
    def test_path_spaces_quoted(self):
        self.assertEqual(build.cmake_literal('C:\\My SDK\\rust.a'), '"C:/My SDK/rust.a"')
    def test_native_names(self):
        self.assertEqual(build.native_filename('win32'), 'pr_pose_core.dll')
        self.assertEqual(build.native_filename('darwin'), 'libpr_pose_core.dylib')
        self.assertEqual(build.native_filename('linux'), 'libpr_pose_core.so')
    def test_emscripten_static_not_unknown_unknown(self):
        args = build.rust_args('wasm', Path('out.a'))
        self.assertIn('wasm32-unknown-emscripten', args)
        self.assertIn('--crate-type=staticlib', args)
        self.assertNotIn('wasm32-unknown-unknown', args)
    def test_no_cross_language_lto(self):
        self.assertIn('-Clto=off', build.rust_args('wasm', Path('out.a')))
    def test_abort_not_unwind(self):
        self.assertIn('-Cpanic=abort', build.rust_args('wasm', Path('out.a')))
    def test_native_is_cdylib(self):
        self.assertIn('--crate-type=cdylib', build.rust_args('native', Path('out.so')))
    def test_rust_tests_use_test_harness(self):
        args = build.rust_args('test', Path('tests'))
        self.assertIn('--test', args)
        self.assertNotIn('-Cpanic=abort', args)
    def test_unknown_build_kind_rejected(self):
        with self.assertRaises(build.lab.LabError): build.rust_args('unknown', Path('x'))
    def test_single_thread_preset_accepted(self):
        build.require_single_thread_preset('SET(PHYSX_WASM_MULTI_THREADING FALSE)')
    def test_threaded_preset_refused(self):
        with self.assertRaises(build.lab.LabError):
            build.require_single_thread_preset('SET(PHYSX_WASM_MULTI_THREADING TRUE)')
    def test_unknown_threading_preset_refused(self):
        for value in ['', 'SET(PHYSX_WASM_MULTI_THREADING FALSE)\nSET(PHYSX_WASM_MULTI_THREADING TRUE)']:
            with self.assertRaises(build.lab.LabError): build.require_single_thread_preset(value)
    def test_legacy_addon_wont_patch_over_rust(self):
        with self.assertRaises(build.lab.LabError):
            build.lab.patch_bulk(self.patch(), '/tmp/old.cpp')
    def test_header_and_rust_symbols_agree_lexically(self):
        # Lexical coverage check, explicitly not a compiled ABI compatibility proof.
        import re
        rust = (ROOT / 'rust/src/lib.rs').read_text()
        header = (ROOT / 'bridge/pr_rust_core.h').read_text()
        rust_names = set(re.findall(r'pub (?:unsafe )?extern "C" fn (prr_\w+)', rust))
        header_names = set(re.findall(r'\b(prr_\w+)\(', header))
        self.assertEqual(rust_names, header_names)
        self.assertEqual(len(rust_names), 13)

if __name__ == '__main__': unittest.main()
