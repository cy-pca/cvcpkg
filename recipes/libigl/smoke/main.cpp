// recipes/libigl/smoke — exercises the installed header-only igl::core bundle.
//
// Besides the FE operators it compiles the code that needs the non-.h/.cpp files
// the recipe copies into include/igl (AABB ray casting includes raytri.c, svd3x3
// the Singular_Value_Decomposition_*.hpp kernels), so a bundle that installs but
// cannot be consumed fails here, and it runs signed distance (fast winding
// number), heat geodesics, marching tets and parallel_for on a unit octahedron.
#include <igl/AABB.h>
#include <igl/Hit.h>
#include <igl/cotmatrix.h>
#include <igl/default_num_threads.h>
#include <igl/doublearea.h>
#include <igl/heat_geodesics.h>
#include <igl/marching_tets.h>
#include <igl/massmatrix.h>
#include <igl/parallel_for.h>
#include <igl/signed_distance.h>
#include <igl/svd3x3.h>

#include <Eigen/Core>
#include <Eigen/Sparse>

#include <atomic>
#include <cmath>
#include <cstdio>
#include <vector>

static_assert(EIGEN_VERSION_AT_LEAST(5, 0, 0), "not the eigen recipe's Eigen 5");

#if defined(__EMSCRIPTEN__) && !defined(__EMSCRIPTEN_PTHREADS__) &&                               \
    !defined(IGL_PARALLEL_FOR_FORCE_SERIAL)
#error "single-threaded wasm consumer got a thread-pool igl::core (no IGL_PARALLEL_FOR_FORCE_SERIAL)"
#endif

namespace {
int failures = 0;

void check(bool ok, const char *what) {
  std::printf("  %s %s\n", ok ? "ok  " : "FAIL", what);
  if (!ok)
    ++failures;
}
} // namespace

int main() {
  // Unit octahedron with outward-oriented faces.
  Eigen::MatrixXd V(6, 3);
  V << 1, 0, 0, -1, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 1, 0, 0, -1;
  Eigen::MatrixXi F(8, 3);
  F << 0, 2, 4, 1, 4, 2, 0, 4, 3, 1, 3, 4, 0, 5, 2, 1, 2, 5, 0, 3, 5, 1, 5, 3;

  // FE operators: rows of L sum to 0, L is symmetric, lumped mass = area.
  Eigen::SparseMatrix<double> L, M;
  igl::cotmatrix(V, F, L);
  igl::massmatrix(V, F, igl::MASSMATRIX_TYPE_VORONOI, M);
  Eigen::VectorXd dblA;
  igl::doublearea(V, F, dblA);
  const double area = 0.5 * dblA.sum();
  const Eigen::SparseMatrix<double> Lt = L.transpose();
  check(Eigen::VectorXd(L * Eigen::VectorXd::Ones(V.rows())).cwiseAbs().maxCoeff() < 1e-12,
        "cotmatrix rows sum to 0");
  check(Eigen::SparseMatrix<double>(L - Lt).norm() < 1e-12, "cotmatrix is symmetric");
  check(std::abs(area - 4.0 * std::sqrt(3.0)) < 1e-12, "doublearea");
  check(std::abs(Eigen::VectorXd(M.diagonal()).sum() - area) < 1e-9, "massmatrix sums to area");

  // AABB: closest point, and a ray cast (ray_mesh_intersect -> raytri.c).
  igl::AABB<Eigen::MatrixXd, 3> tree;
  tree.init(V, F);
  int fi = -1;
  Eigen::RowVector3d c;
  const double sqrd = tree.squared_distance(V, F, Eigen::RowVector3d(2, 0, 0), fi, c);
  check(std::abs(sqrd - 1.0) < 1e-12 && c.isApprox(Eigen::RowVector3d(1, 0, 0)),
        "AABB squared_distance");
  igl::Hit<double> hit;
  const bool hit_any =
      tree.intersect_ray(V, F, Eigen::RowVector3d(0, 0, 0), Eigen::RowVector3d(1, 1, 1), hit);
  check(hit_any && hit.id == 0 && std::abs(hit.t - 1.0 / 3.0) < 1e-6, "AABB intersect_ray");

  // Signed distance, signed by fast winding number: inside < 0 < outside.  The
  // sign factor is 1 - 2|w| with an approximated far-field w, so it scales the
  // distance slightly off 1 away from the surface.
  Eigen::MatrixXd P(2, 3);
  P << 0, 0, 0, 2, 0, 0;
  Eigen::VectorXd S;
  Eigen::VectorXi I;
  Eigen::MatrixXd C, N;
  igl::signed_distance(P, V, F, igl::SIGNED_DISTANCE_TYPE_FAST_WINDING_NUMBER, S, I, C, N);
  check(std::abs(S(0) + 1.0 / std::sqrt(3.0)) < 1e-3 && std::abs(S(1) - 1.0) < 1e-2,
        "signed_distance (fast winding number)");

  // Heat geodesics from +z: 0 at the source, the equator equidistant and nearer
  // than -z.
  igl::HeatGeodesicsData<double> hg;
  const bool hg_ok = igl::heat_geodesics_precompute(V, F, hg);
  Eigen::VectorXd D;
  if (hg_ok)
    igl::heat_geodesics_solve(hg, (Eigen::VectorXi(1) << 4).finished(), D);
  check(hg_ok && std::abs(D(4)) < 1e-9 && D(0) > 0 && std::abs(D(0) - D(2)) < 1e-6 &&
            D(5) > D(0),
        "heat_geodesics");

  // Marching tets: the x = 0.5 level set of x on the corner tet is one triangle.
  Eigen::MatrixXd TV(4, 3);
  TV << 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1;
  Eigen::MatrixXi TT(1, 4);
  TT << 0, 1, 2, 3;
  const Eigen::VectorXd X = TV.col(0);
  Eigen::MatrixXd SV;
  Eigen::MatrixXi SF;
  igl::marching_tets(TV, TT, X, 0.5, SV, SF);
  check(SF.rows() == 1 && SV.rows() == 3 && (SV.col(0).array() - 0.5).abs().maxCoeff() < 1e-12,
        "marching_tets");

  // svd3x3 (Singular_Value_Decomposition_*.hpp): U * S * W^T reproduces A.
  Eigen::Matrix3f A;
  A << 2, -1, 0, 1, 3, 1, 0, 1, 4;
  Eigen::Matrix3f U, W;
  Eigen::Vector3f s;
  igl::svd3x3(A, U, s, W);
  check((U * s.asDiagonal() * W.transpose() - A).cwiseAbs().maxCoeff() < 1e-4f, "svd3x3");

  // parallel_for: the thread pool natively and on wasm-mt, serial on wasm.
  std::vector<int> visits(1000, 0);
  std::atomic<long> sum{0};
  igl::parallel_for(
      1000,
      [&](const int i) {
        visits[i] += 1;
        sum += i;
      },
      1);
  bool once = true;
  for (int v : visits)
    once = once && v == 1;
  check(once && sum == 499500, "parallel_for");

#ifdef IGL_PARALLEL_FOR_FORCE_SERIAL
  const char *backend = "serial";
#else
  const char *backend = "pool";
#endif
  std::printf("igl_smoke: Eigen %d.%d.%d, parallel_for %s (%u threads), %d failure(s)\n",
              EIGEN_MAJOR_VERSION, EIGEN_MINOR_VERSION, EIGEN_PATCH_VERSION, backend,
              igl::default_num_threads(), failures);
  return failures == 0 ? 0 : 1;
}
